import { useEffect, useState } from "react";
import { Button } from "antd";
import { PictureOutlined } from "@ant-design/icons";

type Props = {
  src?: string | null;
  alt: string;
  size?: number;
  /**
   * 占位是否显示「暂无主图」文字（8a-2）。默认 false：占位只有图标，与原来完全一致。
   * 框宽 ≤ 40px 时文字分两行「暂无」/「主图」并隐藏图标。
   */
  emptyText?: boolean;
};

/**
 * 款式缩略图：点击在新窗口看原图。
 *
 * 对所有调用方生效（8a-2，设计 §7.5）：`<img>` 不带 Referer（外部链接常有防盗链），
 * 加载失败（防盗链、https 页面里的 http 图被拦）回落到占位。
 */
export function StyleImageThumbnail({ src, alt, size = 48, emptyText = false }: Props) {
  const [failed, setFailed] = useState(false);
  // 换了一张图就重新尝试加载
  useEffect(() => {
    setFailed(false);
  }, [src]);

  const frameStyle = {
    width: size,
    height: size,
    borderRadius: 6,
    border: "1px solid #d9d9d9",
    overflow: "hidden",
    flex: "0 0 auto",
  } as const;

  if (!src || failed) {
    const narrow = size <= 40;
    return (
      <div
        style={{
          ...frameStyle,
          display: "grid",
          placeItems: "center",
          alignContent: "center",
          color: "#8c8c8c",
          background: "#fafafa",
          textAlign: "center",
        }}
        aria-label={`${alt}暂无主图`}
        title="暂无主图"
      >
        {emptyText && narrow ? null : <PictureOutlined aria-hidden />}
        {emptyText ? (
          <span aria-hidden style={{ fontSize: 11, lineHeight: "12px", color: "#595959" }}>
            {narrow ? (
              <>
                暂无
                <br />
                主图
              </>
            ) : (
              "暂无主图"
            )}
          </span>
        ) : null}
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
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
    </Button>
  );
}
