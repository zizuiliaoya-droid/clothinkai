import { Button } from "antd";
import { PictureOutlined } from "@ant-design/icons";

type Props = {
  src?: string | null;
  alt: string;
  size?: number;
};

export function StyleImageThumbnail({ src, alt, size = 48 }: Props) {
  const frameStyle = {
    width: size,
    height: size,
    borderRadius: 6,
    border: "1px solid #d9d9d9",
    overflow: "hidden",
    flex: "0 0 auto",
  } as const;

  if (!src) {
    return (
      <div
        style={{ ...frameStyle, display: "grid", placeItems: "center", color: "#8c8c8c", background: "#fafafa" }}
        aria-label={`${alt}暂无主图`}
        title="暂无主图"
      >
        <PictureOutlined aria-hidden />
      </div>
    );
  }

  return (
    <Button
      type="text"
      href={src}
      target="_blank"
      rel="noreferrer"
      aria-label={`查看${alt}原图`}
      title="点击查看原图"
      style={{ ...frameStyle, padding: 0, display: "block" }}
    >
      <img
        src={src}
        alt={alt}
        width={size}
        height={size}
        loading="lazy"
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
    </Button>
  );
}