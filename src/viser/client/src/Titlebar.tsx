import { ViewerContext } from "./ViewerContext";
import {
  RegisterTitlebarItemMessage,
  ThemeConfigurationMessage,
} from "./WebsocketMessages";
import { htmlIconWrapper } from "./components/ComponentStyles.css";
import {
  ActionIcon,
  Burger,
  Button,
  Container,
  Divider,
  Group,
  Paper,
  Box,
  useMantineColorScheme,
  Portal,
  Tooltip,
} from "@mantine/core";
import {
  IconBrandGithub,
  IconFileDescription,
  IconKeyboard,
} from "@tabler/icons-react";
import { useDisclosure } from "@mantine/hooks";
import { useContext, useMemo } from "react";

// Type helpers.
type ArrayElement<ArrayType extends readonly unknown[]> =
  ArrayType extends readonly (infer ElementType)[] ? ElementType : never;
type TitlebarContent = NonNullable<
  ThemeConfigurationMessage["titlebar_content"]
>;
function assertUnreachable(x: never): never {
  throw new Error("Didn't expect to get here", x);
}

function getIcon(
  icon: ArrayElement<NonNullable<TitlebarContent["buttons"]>>["icon"],
) {
  let Icon = null;
  switch (icon) {
    case null:
      break;
    case "GitHub":
      Icon = IconBrandGithub;
      break;
    case "Description":
      Icon = IconFileDescription;
      break;
    case "Keyboard":
      Icon = IconKeyboard;
      break;
    default:
      assertUnreachable(icon);
  }
  return Icon;
}

// We inherit props directly from message contents.
export function TitlebarButton(
  props: ArrayElement<NonNullable<TitlebarContent["buttons"]>>,
) {
  const Icon = getIcon(props.icon);
  return (
    <Button
      component="a"
      variant="default"
      size="compact-sm"
      href={props.href || undefined}
      target="_blank"
      leftSection={Icon === null ? null : <Icon size="1em" />}
      ml="xs"
      color="gray"
    >
      {props.text}
    </Button>
  );
}

export function MobileTitlebarButton(
  props: ArrayElement<NonNullable<TitlebarContent["buttons"]>>,
) {
  const Icon = getIcon(props.icon);
  return (
    <Button
      m="sm"
      component="a"
      variant="default"
      href={props.href || undefined}
      target="_blank"
      leftSection={Icon === null ? null : <Icon size="1.5em" />}
      ml="sm"
      color="gray"
    >
      {props.text}
    </Button>
  );
}

export function TitlebarImage(
  props: NonNullable<TitlebarContent["image"]>,
  colorScheme: string,
) {
  let imageSource: string;
  if (props.image_url_dark == null || colorScheme === "light") {
    imageSource = props.image_url_light;
  } else {
    imageSource = props.image_url_dark;
  }
  const image = (
    <img
      src={imageSource}
      alt={props.image_alt}
      style={{
        height: "1.8em",
        margin: "0 0.5em",
      }}
    />
  );

  if (props.href == null) {
    return image;
  }
  return (
    <a style={{ display: "block", height: "1.8em" }} href={props.href}>
      {image}
    </a>
  );
}

/** A custom titlebar item (button or vertical separator) registered from
 * Python via `add_titlebar_button` / `add_titlebar_separator`. */
function TitlebarItem({ uuid, props }: RegisterTitlebarItemMessage) {
  const viewer = useContext(ViewerContext)!;
  if (props._kind === "separator") {
    return <Divider orientation="vertical" my="0.6em" />;
  }
  const onClick = () =>
    viewer.mutable.current.sendMessage({
      type: "TitlebarButtonClickMessage",
      uuid,
    });
  const button =
    props._icon_html !== null ? (
      <ActionIcon
        variant="subtle"
        color="gray"
        size="md"
        disabled={props.disabled}
        onClick={onClick}
        aria-label={props.tooltip ?? undefined}
      >
        <div
          className={htmlIconWrapper}
          dangerouslySetInnerHTML={{ __html: props._icon_html }}
        />
      </ActionIcon>
    ) : (
      <Button
        variant="subtle"
        color="gray"
        size="compact-sm"
        disabled={props.disabled}
        onClick={onClick}
      >
        {props.text}
      </Button>
    );
  if (props.tooltip === null) return button;
  return (
    <Tooltip label={props.tooltip} withinPortal>
      {button}
    </Tooltip>
  );
}

export function Titlebar() {
  const viewer = useContext(ViewerContext)!;
  const content = viewer.useGui((state) => state.theme.titlebar_content);
  const titlebarItems = viewer.useGui((state) => state.titlebarItems);
  const colorScheme = useMantineColorScheme().colorScheme;

  const [burgerOpen, burgerHandlers] = useDisclosure(false);

  const customItems = useMemo(
    () =>
      Object.values(titlebarItems)
        .filter((item) => item.props.visible)
        .sort((a, b) => a.props.order - b.props.order),
    [titlebarItems],
  );

  // Custom items keep the titlebar visible even without `titlebar_content`.
  if (content == null && customItems.length === 0) {
    return null;
  }

  const buttons = content?.buttons ?? null;
  const imageData = content?.image ?? null;

  return (
    <Box
      style={{
        height: "3.2em",
        margin: 0,
        border: "0",
        zIndex: 10,
      }}
    >
      <Paper shadow="0 0 0.8em 0 rgba(0,0,0,0.1)" style={{ height: "100%" }}>
        <Container
          fluid
          style={() => ({
            height: "100%",
            display: "flex",
            alignItems: "center",
          })}
        >
          <Group
            gap="xs"
            wrap="nowrap"
            style={() => ({ marginRight: "auto", height: "100%" })}
          >
            {imageData !== null ? TitlebarImage(imageData, colorScheme) : null}
            {customItems.map((item) => (
              <TitlebarItem {...item} key={item.uuid} />
            ))}
          </Group>
          <Group
            display={{ base: "none", xs: "flex" }}
            style={() => ({
              flexWrap: "nowrap",
              overflowX: "scroll",
              msOverflowStyle: "none",
              scrollbarWidth: "none",
            })}
          >
            {buttons?.map((btn, index) => (
              <TitlebarButton {...btn} key={index} />
            ))}
          </Group>
          <Burger
            size="sm"
            opened={burgerOpen}
            onClick={burgerHandlers.toggle}
            title={!burgerOpen ? "Open navigation" : "Close navigation"}
            display={{ base: "block", xs: "none" }}
          ></Burger>
        </Container>
        <Portal>
          <Paper
            display={{ base: "flex", xs: "none" }}
            radius="0"
            style={{
              flexDirection: "column",
              position: "absolute",
              top: "3.2em",
              zIndex: 2000,
              height: burgerOpen ? "calc(100vh - 2.375em)" : "0",
              width: "100vw",
              transition: "all 0.5s",
              overflow: burgerOpen ? "scroll" : "hidden",
              padding: burgerOpen ? "1rem" : "0",
            }}
          >
            {buttons?.map((btn, index) => (
              <MobileTitlebarButton {...btn} key={index} />
            ))}
          </Paper>
        </Portal>
      </Paper>
    </Box>
  );
}
