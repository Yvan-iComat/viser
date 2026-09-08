"""Convert a ROS ``.xacro`` (or ``.urdf.xacro``) file into plain URDF XML.

Standalone: needs no ROS installation and no ``catkin``/``colcon`` workspace.

Why this exists
---------------
``yourdfpy`` (and therefore :class:`viser.extras.ViserUrdf`) only reads plain
URDF. A ``.xacro`` file is a macro-expansion template, so it must be flattened
first. The usual tool is ROS's ``xacro`` CLI, which is unavailable on a plain
Windows Python install.

What is supported
-----------------
* ``<xacro:include>``, including ``$(find <pkg>)`` paths resolved via
  ``--package-path`` search roots
* ``<xacro:property>`` and ``${...}`` expression substitution (arithmetic and
  property/arg references)
* ``<xacro:arg>`` defaults plus ``--arg name:=value`` overrides, via ``$(arg)``
* ``<xacro:macro>`` definitions and their instantiation, with parameters,
  default values, and ``<xacro:insert_block>``
* ``$(find pkg)`` rewriting inside ``mesh filename`` attributes so meshes
  resolve on disk

Two backends
------------
By default the real ``xacro`` package is used when importable (most faithful).
Pass ``--backend builtin`` to force the bundled minimal expander, which covers
the common subset above.

Usage
-----
    python xacro_to_urdf.py model.urdf.xacro --output model.urdf
    python xacro_to_urdf.py model.urdf.xacro --output model.urdf \
        --package-path ../ros_ws/src --arg prefix:=left_

``--mesh-paths copy`` also copies the referenced meshes next to the output, so
the result is self-contained and loads from any directory::

    python xacro_to_urdf.py model.urdf.xacro --output out/model.urdf \
        --package-path ../ros_ws/src --mesh-paths copy --check

Always pass ``--check`` to confirm the result actually loads before trusting it.
An incomplete expansion (missing macro packages) is refused rather than written,
since a hollow URDF loads cleanly but is missing most of the robot.
"""

from __future__ import annotations

import math
import re
import sys
import xml.dom.minidom
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tyro

XACRO_NS = "http://www.ros.org/wiki/xacro"
ET.register_namespace("xacro", XACRO_NS)

# Matches $(find pkg), $(arg name), $(optenv ...) style substitution args.
_DOLLAR_PAREN = re.compile(r"\$\(\s*(\w+)\s+([^)]*?)\s*\)")
# Matches ${expression} property/expression substitution.
_DOLLAR_BRACE = re.compile(r"\$\{([^}]*)\}")


class XacroError(RuntimeError):
    """Raised when a xacro file cannot be expanded."""


# --------------------------------------------------------------------------
# Package resolution
# --------------------------------------------------------------------------


def find_package_dirs(search_roots: list[Path]) -> dict[str, Path]:
    """Map ROS package name -> directory, by locating package.xml files.

    Also treats a directory that merely *looks* like a package (has urdf/ or
    meshes/) as a package, so vendored mesh folders without a package.xml
    still resolve.
    """
    packages: dict[str, Path] = {}
    for root in search_roots:
        if not root.is_dir():
            continue
        for manifest in root.rglob("package.xml"):
            try:
                name_el = ET.parse(manifest).getroot().find("name")
            except ET.ParseError:
                continue
            if name_el is not None and name_el.text:
                packages.setdefault(name_el.text.strip(), manifest.parent)
        # Fallback: directory-name matching for non-ROS vendored trees.
        for cand in [root, *root.iterdir()] if root.is_dir() else []:
            if cand.is_dir() and (
                (cand / "urdf").is_dir() or (cand / "meshes").is_dir()
            ):
                packages.setdefault(cand.name, cand)
    return packages


def resolve_find(pkg: str, packages: dict[str, Path], strict: bool) -> str:
    """Resolve ``$(find pkg)`` to a directory path."""
    if pkg in packages:
        return packages[pkg].as_posix()
    if strict:
        raise XacroError(
            f"Cannot resolve $(find {pkg}): package not found.\n"
            f"  Known packages: {sorted(packages) or '(none)'}\n"
            f"  Pass --package-path pointing at a directory that contains it."
        )
    # Leave a recognizable token so mesh paths stay debuggable.
    return f"package://{pkg}"


# --------------------------------------------------------------------------
# Expression evaluation
# --------------------------------------------------------------------------

_SAFE_FUNCS: dict[str, Any] = {
    k: getattr(math, k)
    for k in (
        "pi",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sqrt",
        "radians",
        "degrees",
        "fabs",
        "floor",
        "ceil",
    )
}
_SAFE_FUNCS.update(
    {"True": True, "False": False, "min": min, "max": max, "abs": abs, "round": round}
)


def eval_expr(expr: str, symbols: dict[str, Any]) -> Any:
    """Evaluate a ``${...}`` expression against known properties.

    Falls back to returning the raw text when the expression references
    something we do not know, so partial expansion never hard-fails.
    """
    expr = expr.strip()
    if not expr:
        return ""
    # Bare symbol lookup is the common case.
    if expr in symbols:
        return symbols[expr]
    env = {**_SAFE_FUNCS, **symbols}
    try:
        return eval(expr, {"__builtins__": {}}, env)  # noqa: S307
    except Exception:
        return "${" + expr + "}"


@dataclass
class Context:
    """Expansion state: properties, args, macros, and package lookup."""

    properties: dict[str, Any] = field(default_factory=dict)
    args: dict[str, str] = field(default_factory=dict)
    macros: dict[str, ET.Element] = field(default_factory=dict)
    packages: dict[str, Path] = field(default_factory=dict)
    strict: bool = False
    # Names of includes/macros we could not resolve. A non-empty list means the
    # output is incomplete even if some links were produced.
    unresolved: list[str] = field(default_factory=list)

    def symbols(self) -> dict[str, Any]:
        return {**self.args, **self.properties}

    def child(self) -> Context:
        """A scope that inherits definitions but isolates new properties."""
        return Context(
            properties=dict(self.properties),
            args=self.args,
            macros=self.macros,
            packages=self.packages,
            strict=self.strict,
            unresolved=self.unresolved,  # shared, so nested failures surface
        )


def substitute(text: str, ctx: Context) -> str:
    """Apply both ``$(...)`` and ``${...}`` substitution to a string."""
    if not text or ("$" not in text):
        return text

    def _paren(m: re.Match[str]) -> str:
        kind, rest = m.group(1), m.group(2).strip()
        if kind == "find":
            return resolve_find(rest, ctx.packages, ctx.strict)
        if kind == "arg":
            if rest in ctx.args:
                return str(ctx.args[rest])
            if ctx.strict:
                raise XacroError(f"Undefined $(arg {rest})")
            return ""
        if kind == "optenv":
            parts = rest.split(None, 1)
            import os

            return os.environ.get(parts[0], parts[1] if len(parts) > 1 else "")
        if kind == "eval":
            return str(eval_expr(rest, ctx.symbols()))
        return m.group(0)

    text = _DOLLAR_PAREN.sub(_paren, text)

    # Repeat brace substitution so nested properties settle.
    for _ in range(10):
        if "${" not in text:
            break
        new = _DOLLAR_BRACE.sub(
            lambda m: str(eval_expr(m.group(1), ctx.symbols())), text
        )
        if new == text:
            break
        text = new
    return text


# --------------------------------------------------------------------------
# Builtin expander
# --------------------------------------------------------------------------


def _tag(el: ET.Element) -> str:
    """Local tag name, namespace stripped."""
    t = el.tag
    if isinstance(t, str) and t.startswith("{"):
        return t.split("}", 1)[1]
    return t


def _xacro_name(el: ET.Element) -> str | None:
    """If this element is any xacro:* element, return the local name."""
    t = el.tag
    if not isinstance(t, str):
        return None
    if t.startswith(f"{{{XACRO_NS}}}"):
        return t.split("}", 1)[1]
    if t.startswith("xacro:"):
        return t.split(":", 1)[1]
    return None


def collect_definitions(
    root: ET.Element, base_dir: Path, ctx: Context
) -> list[ET.Element]:
    """Recursively process includes and hoist property/arg/macro definitions.

    Returns the flattened list of content nodes (definitions removed).
    """
    content: list[ET.Element] = []
    for el in list(root):
        kind = _xacro_name(el)
        if kind == "include":
            fname = substitute(el.get("filename", ""), ctx)
            if fname.startswith("package://"):
                # Unresolved $(find): don't join this onto base_dir, that
                # produces a nonsense "dir/package:/pkg/..." path.
                pkg = fname[len("package://") :].split("/", 1)[0]
                msg = (
                    f"Cannot resolve include {fname}\n"
                    f"  Package '{pkg}' was not found. Pass --package-path pointing\n"
                    f"  at a directory that contains it."
                )
                if ctx.strict:
                    raise XacroError(msg)
                print(f"  warning: {msg}", file=sys.stderr)
                ctx.unresolved.append(f"include {fname}")
                continue
            inc = (
                (base_dir / fname).resolve()
                if not Path(fname).is_absolute()
                else Path(fname)
            )
            if not inc.exists():
                if ctx.strict:
                    raise XacroError(f"Include not found: {inc}\n  (from {base_dir})")
                print(f"  warning: skipping missing include {inc}", file=sys.stderr)
                ctx.unresolved.append(f"include {inc}")
                continue
            sub_root = ET.parse(inc).getroot()
            content.extend(collect_definitions(sub_root, inc.parent, ctx))
        elif kind == "property":
            name = el.get("name", "")
            if "value" in el.attrib:
                val = substitute(el.get("value", ""), ctx)
                # Keep numbers numeric so arithmetic works.
                try:
                    ctx.properties[name] = int(val)
                except ValueError:
                    try:
                        ctx.properties[name] = float(val)
                    except ValueError:
                        ctx.properties[name] = val
            else:
                ctx.properties[name] = el  # block property
        elif kind == "arg":
            name = el.get("name", "")
            if name not in ctx.args:  # CLI overrides win
                ctx.args[name] = substitute(el.get("default", ""), ctx)
        elif kind == "macro":
            ctx.macros[el.get("name", "")] = el
        else:
            content.append(el)
    return content


def expand_node(
    el: ET.Element, ctx: Context, blocks: dict[str, list[ET.Element]]
) -> list[ET.Element]:
    """Expand one element, returning zero or more concrete elements."""
    kind = _xacro_name(el)

    if kind == "insert_block":
        return [
            c
            for b in blocks.get(el.get("name", ""), [])
            for c in expand_node(b, ctx, blocks)
        ]

    if kind in ("if", "unless"):
        cond = substitute(el.get("value", ""), ctx)
        truth = str(cond).strip().lower() in ("1", "true", "yes")
        if kind == "unless":
            truth = not truth
        if not truth:
            return []
        out: list[ET.Element] = []
        for c in list(el):
            out.extend(expand_node(c, ctx, blocks))
        return out

    if kind == "property":
        name = el.get("name", "")
        ctx.properties[name] = substitute(el.get("value", ""), ctx)
        return []

    if kind in ("include", "macro", "arg"):
        return []  # already hoisted

    if kind is not None and kind in ctx.macros:
        return instantiate_macro(ctx.macros[kind], el, ctx, blocks)

    if kind is not None:
        # An undefined macro call means the file defining it never loaded, so
        # the robot's geometry is missing. Record it rather than silently
        # emitting a hollow URDF.
        print(f"  warning: dropping unresolved <xacro:{kind}>", file=sys.stderr)
        ctx.unresolved.append(f"macro {kind}")
        return []

    # Plain element: copy with substituted attributes, recurse into children.
    new = ET.Element(_tag(el))
    for k, v in el.attrib.items():
        new.set(k, substitute(v, ctx))
    if el.text and el.text.strip():
        new.text = substitute(el.text, ctx)
    for c in list(el):
        for expanded in expand_node(c, ctx, blocks):
            new.append(expanded)
    return [new]


def instantiate_macro(
    macro: ET.Element,
    call: ET.Element,
    ctx: Context,
    outer_blocks: dict[str, list[ET.Element]],
) -> list[ET.Element]:
    """Instantiate a ``<xacro:macro>`` at a call site."""
    scope = ctx.child()
    params_spec = (macro.get("params") or "").split()

    # Block parameters are prefixed with * in the params list.
    blocks: dict[str, list[ET.Element]] = {}
    call_children = list(call)
    block_names = [p.lstrip("*") for p in params_spec if p.startswith("*")]
    for i, bname in enumerate(block_names):
        if i < len(call_children):
            blocks[bname] = [call_children[i]]

    # Named children may also supply blocks (e.g. <ext_axes_.../> style).
    for c in call_children:
        blocks.setdefault(_tag(c), [c])
    blocks.update(outer_blocks)

    for spec in params_spec:
        if spec.startswith("*"):
            continue
        if ":=" in spec:
            pname, default = spec.split(":=", 1)
            scope.properties[pname] = substitute(default.lstrip("^"), ctx)
        else:
            pname = spec
            scope.properties.setdefault(pname, "")
        if pname in call.attrib:
            scope.properties[pname] = substitute(call.get(pname, ""), ctx)

    # Any extra attributes on the call site become properties too.
    for k, v in call.attrib.items():
        scope.properties[k] = substitute(v, ctx)

    out: list[ET.Element] = []
    for c in list(macro):
        out.extend(expand_node(c, scope, blocks))
    return out


def expand_builtin(
    path: Path, args: dict[str, str], packages: dict[str, Path], strict: bool
) -> tuple[ET.Element, list[str]]:
    """Expand a xacro file using the bundled minimal expander.

    Returns the ``<robot>`` element and a list of unresolved includes/macros.
    """
    ctx = Context(args=dict(args), packages=packages, strict=strict)
    root = ET.parse(path).getroot()
    content = collect_definitions(root, path.parent, ctx)

    out_root = ET.Element("robot")
    name = substitute(root.get("name", path.stem.replace(".urdf", "")), ctx)
    out_root.set("name", name)
    for el in content:
        for expanded in expand_node(el, ctx, {}):
            out_root.append(expanded)
    return out_root, ctx.unresolved


# --------------------------------------------------------------------------
# Real-xacro backend
# --------------------------------------------------------------------------


def expand_with_xacro_pkg(
    path: Path, args: dict[str, str], packages: dict[str, Path]
) -> ET.Element:
    """Expand using the actual ``xacro`` package, if it is importable."""
    import xacro  # noqa: PLC0415

    # Teach xacro how to resolve $(find pkg) without a ROS workspace. Upstream
    # routes this through ament_index_python, which only exists under ROS, so
    # point _eval_find at our own package map instead.
    def _eval_find(pkg: str) -> str:
        if pkg in packages:
            return packages[pkg].as_posix()
        raise XacroError(
            f"Cannot resolve $(find {pkg}): package not found.\n"
            f"  Known packages: {sorted(packages) or '(none)'}\n"
            f"  Pass --package-path pointing at a directory that contains it."
        )

    try:
        import xacro.substitution_args as sub  # noqa: PLC0415

        sub._eval_find = _eval_find  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass

    doc = xacro.process_file(str(path), mappings={k: str(v) for k, v in args.items()})
    return ET.fromstring(doc.toxml())


# --------------------------------------------------------------------------
# Post-processing
# --------------------------------------------------------------------------


def rewrite_mesh_paths(
    root: ET.Element, packages: dict[str, Path], out_dir: Path, mode: str
) -> int:
    """Normalize mesh filenames so a URDF loader can find them.

    ``mode='package'`` keeps ``package://pkg/...`` URIs (yourdfpy's
    filename_handler_magic resolves these relative to the URDF directory).
    ``mode='relative'`` rewrites to paths relative to the output URDF.
    """
    changed = 0
    for mesh in root.iter("mesh"):
        fn = mesh.get("filename")
        if not fn:
            continue
        orig = fn
        # An absolute path that landed inside a known package -> package:// URI.
        if mode == "package" and not fn.startswith("package://"):
            p = Path(fn)
            for pkg, pdir in packages.items():
                try:
                    rel = p.resolve().relative_to(pdir.resolve())
                except (ValueError, OSError):
                    continue
                fn = f"package://{pkg}/{rel.as_posix()}"
                break
        elif mode == "relative":
            if fn.startswith("package://"):
                rest = fn[len("package://") :]
                pkg, _, tail = rest.partition("/")
                if pkg in packages:
                    fn = (packages[pkg] / tail).resolve().as_posix()
            p = Path(fn)
            if p.is_absolute():
                try:
                    fn = p.relative_to(out_dir.resolve()).as_posix()
                except ValueError:
                    fn = p.as_posix()
        if fn != orig:
            mesh.set("filename", fn)
            changed += 1
    return changed


def copy_meshes_next_to_output(
    root: ET.Element, packages: dict[str, Path], out_dir: Path
) -> int:
    """Copy referenced meshes into ``<out_dir>/meshes/`` and repoint the URDF.

    This makes the output self-contained, which is what
    ``filename_handler_magic`` needs when the URDF does not live inside the
    original ROS package.
    """
    import shutil  # noqa: PLC0415

    mesh_dir = out_dir / "meshes"
    copied = 0
    for mesh in root.iter("mesh"):
        fn = mesh.get("filename")
        if not fn:
            continue
        src: Path | None = None
        if fn.startswith("package://"):
            rest = fn[len("package://") :]
            pkg, _, tail = rest.partition("/")
            if pkg in packages:
                src = packages[pkg] / tail
        else:
            p = Path(fn)
            if p.is_absolute() and p.exists():
                src = p
        if src is None or not src.exists():
            continue
        mesh_dir.mkdir(parents=True, exist_ok=True)
        dst = mesh_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
            copied += 1
        mesh.set("filename", f"meshes/{src.name}")
    return copied


def prettify(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    pretty = xml.dom.minidom.parseString(raw).toprettyxml(indent="  ")
    # Drop the blank lines minidom sprinkles in.
    lines = [ln for ln in pretty.splitlines() if ln.strip()]
    return "\n".join(lines) + "\n"


def check_urdf(path: Path) -> bool:
    """Load the output with yourdfpy to prove it is usable."""
    try:
        from functools import partial  # noqa: PLC0415

        import yourdfpy  # noqa: PLC0415
    except ImportError:
        print("  --check skipped: yourdfpy not installed", file=sys.stderr)
        return True
    try:
        urdf = yourdfpy.URDF.load(
            str(path),
            build_scene_graph=True,
            load_meshes=True,
            filename_handler=partial(yourdfpy.filename_handler_magic, dir=path.parent),
        )
    except Exception as e:
        print(f"  CHECK FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    n_geom = len(urdf.scene.geometry) if urdf.scene is not None else 0
    print(
        f"  check OK: {len(urdf.robot.links)} links, "
        f"{len(urdf.actuated_joint_names)} actuated joints, {n_geom} mesh geometries"
    )
    if n_geom == 0:
        print("  warning: no mesh geometry loaded - check mesh paths", file=sys.stderr)
    return True


def main(
    input: Path,
    /,
    output: Path | None = None,
    package_path: list[Path] = [],
    arg: list[str] = [],
    backend: Literal["auto", "xacro", "builtin"] = "auto",
    mesh_paths: Literal["package", "relative", "copy", "keep"] = "package",
    strict: bool = False,
    check: bool = False,
) -> None:
    """Convert a .xacro file to plain URDF XML.

    Args:
        input: Path to the input .xacro / .urdf.xacro file.
        output: Output .urdf path. Defaults to the input with .xacro stripped.
        package_path: Extra roots to search for ROS packages ($(find ...)).
                      The input's own parent and grandparent are always searched.
        arg: Xacro args as ``name:=value`` (repeatable).
        backend: ``xacro`` uses the real package, ``builtin`` the bundled
                 expander, ``auto`` prefers the real one when importable.
        mesh_paths: How to write mesh filenames in the output.
        strict: Fail on unresolved includes/args instead of warning.
        check: Load the result with yourdfpy to verify it works.
    """
    if not input.exists():
        raise SystemExit(f"Input not found: {input}")

    out_path = output or input.parent / (
        input.name.replace(".xacro", "") or f"{input.stem}.urdf"
    )
    if out_path.suffix != ".urdf":
        out_path = out_path.with_suffix(".urdf")

    cli_args: dict[str, str] = {}
    for a in arg:
        if ":=" not in a:
            raise SystemExit(f"--arg must look like name:=value, got {a!r}")
        k, v = a.split(":=", 1)
        cli_args[k] = v

    search_roots = [input.parent, input.parent.parent, *package_path]
    packages = find_package_dirs(search_roots)
    print(f"Converting {input}")
    print(f"  packages found: {sorted(packages) or '(none)'}")

    use_xacro = backend == "xacro"
    if backend == "auto":
        try:
            import xacro  # noqa: F401, PLC0415

            use_xacro = True
        except ImportError:
            use_xacro = False

    unresolved: list[str] = []
    if use_xacro:
        print("  backend: xacro package")
        try:
            root = expand_with_xacro_pkg(input, cli_args, packages)
        except Exception as e:
            if backend == "xacro":
                raise SystemExit(
                    f"xacro backend failed: {type(e).__name__}: {e}"
                ) from e
            print(
                f"  xacro backend failed ({type(e).__name__}), using builtin",
                file=sys.stderr,
            )
            try:
                root, unresolved = expand_builtin(input, cli_args, packages, strict)
            except XacroError as e2:
                raise SystemExit(f"\n  ERROR: {e2}") from None
    else:
        print("  backend: builtin expander")
        try:
            root, unresolved = expand_builtin(input, cli_args, packages, strict)
        except XacroError as e:
            # Expected, actionable failure: report it without a traceback.
            raise SystemExit(f"\n  ERROR: {e}") from None

    n_links = len(root.findall("link"))
    n_joints = len(root.findall("joint"))

    # An incomplete expansion is worse than a failed one: it writes a URDF that
    # loads fine but is missing most of the robot. Refuse to write it.
    if unresolved:
        missing = "\n".join(f"    - {u}" for u in dict.fromkeys(unresolved))
        print(
            f"\n  ERROR: expansion is incomplete - {len(set(unresolved))} unresolved item(s):\n"
            f"{missing}\n"
            f"  Only {n_links} link(s) and {n_joints} joint(s) were produced, so the\n"
            f"  robot's geometry is missing. Nothing was written.\n\n"
            f"  The macros defining this robot live in a ROS package that is not here.\n"
            f"  Fetch it, then point --package-path at its parent directory, e.g.:\n"
            f"    git clone <robot support repo> ./ros_src\n"
            f"    python {Path(__file__).name} {input.name} --package-path ./ros_src\n"
            f"  (for the KUKA KR120: the kuka_quantec_support package)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if n_links == 0:
        print(
            "\n  ERROR: expansion produced 0 links; nothing written.", file=sys.stderr
        )
        raise SystemExit(2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if mesh_paths == "copy":
        n = copy_meshes_next_to_output(root, packages, out_path.parent)
        print(f"  meshes copied next to output: {n}")
    elif mesh_paths != "keep":
        n = rewrite_mesh_paths(root, packages, out_path.parent, mesh_paths)
        print(f"  mesh paths rewritten: {n}")

    out_path.write_text(prettify(root), encoding="utf-8")
    print(f"  wrote {out_path}  ({n_links} links, {n_joints} joints)")

    if check and not check_urdf(out_path):
        raise SystemExit(1)


if __name__ == "__main__":
    tyro.cli(main)
