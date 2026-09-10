import React from "react";
import * as THREE from "three";
import { ViserStandardMeshMaterial, ShadowMesh } from "./MeshUtils";
import { MeshMessage } from "../WebsocketMessages";
import { OutlinesIfHovered } from "../OutlinesIfHovered";
import { normalizeScale } from "../utils/normalizeScale";
import {
  syncBufferGeometry,
  type AttributeSpec,
} from "../utils/bufferGeometrySync";
import { srgbUint8ToLinearFloat32 } from "../utils/vertexColors";

/**
 * Component for rendering basic THREE.js meshes
 */
export const BasicMesh = React.forwardRef<
  THREE.Group,
  MeshMessage & { children?: React.ReactNode }
>(function BasicMesh(
  { children, ...message },
  ref: React.ForwardedRef<THREE.Group>,
) {
  // Persistent geometry, synced in place: a streaming/deforming mesh reuses the
  // existing GL buffers via bufferSubData instead of allocating a new
  // BufferGeometry on every vertex/face update. Kept imperative (run during
  // render via useMemo) because the geometry is read synchronously below for
  // OutlinesIfHovered heuristics and the shadow mesh.
  const geometryRef = React.useRef<THREE.BufferGeometry | null>(null);
  if (geometryRef.current === null) {
    geometryRef.current = new THREE.BufferGeometry();
  }
  const geometry = geometryRef.current;
  // Linear-space copy of the sRGB vertex colors, held across updates so that a
  // streaming recolor reuses one buffer instead of allocating per update.
  const linearColorsRef = React.useRef<Float32Array | null>(null);
  React.useMemo(() => {
    // Vertices and faces arrive as Float32Array / Uint32Array views.
    const attributes: Record<string, AttributeSpec> = {
      position: { array: message.props.vertices, itemSize: 3 },
    };
    const vertexColors = message.props.vertex_colors;
    if (vertexColors !== null) {
      linearColorsRef.current = srgbUint8ToLinearFloat32(
        vertexColors,
        linearColorsRef.current,
      );
      attributes.color = { array: linearColorsRef.current, itemSize: 3 };
    }
    const reallocated = syncBufferGeometry(
      geometry,
      attributes,
      message.props.faces,
    );
    // On realloc (e.g. vertex-count change), drop the stale 'normal' attribute:
    // computeVertexNormals reuses an existing attribute without a size check,
    // which would leave normals mismatched with the new position count.
    if (reallocated) {
      geometry.deleteAttribute("normal");
    }
    // syncBufferGeometry only touches the attributes it is handed, so turning
    // vertex colors back off has to drop 'color' here -- otherwise it stays
    // bound to the geometry after the material stops declaring USE_COLOR.
    if (vertexColors === null) {
      geometry.deleteAttribute("color");
      linearColorsRef.current = null;
    }
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
  }, [
    geometry,
    message.props.vertices,
    message.props.faces,
    message.props.vertex_colors,
  ]);

  // Clean up geometry when it changes.
  React.useEffect(() => {
    return () => {
      if (geometry) geometry.dispose();
    };
  }, [geometry]);

  // Check if we should render a shadow mesh.
  const shadowOpacity =
    typeof message.props.receive_shadow === "number"
      ? message.props.receive_shadow
      : 0.0;

  return (
    <group ref={ref}>
      <mesh
        geometry={geometry}
        scale={normalizeScale(message.props.scale)}
        castShadow={message.props.cast_shadow}
        receiveShadow={message.props.receive_shadow === true}
      >
        <ViserStandardMeshMaterial
          {...message.props}
          vertexColors={message.props.vertex_colors !== null}
        />
        <OutlinesIfHovered
          enableCreaseAngle={
            geometry.attributes.position.count < 1024 &&
            geometry.boundingSphere!.radius > 0.1
          }
        />
      </mesh>
      <ShadowMesh
        opacity={shadowOpacity}
        geometry={geometry}
        scale={normalizeScale(message.props.scale)}
      />
      {children}
    </group>
  );
});
