import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import type { GoodsImage } from "@/features/product/types";

type Props = {
  images: GoodsImage[];
  /** 商品显示名（有简称用简称），用于 alt：「显示名 · 款号」。 */
  displayName: string;
  size?: number;
};

/**
 * 商品图（8a-2，设计 §7.5）：由成员款式派生的图并排显示（间距 4、可换行）。
 * 单品最多 1 张；套装缺图的成员不占位；都没有显示一个「暂无主图」占位。
 */
export function GoodsImages({ images, displayName, size = 40 }: Props) {
  if (images.length === 0) {
    return <StyleImageThumbnail src={null} alt={displayName} size={size} emptyText />;
  }
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {images.map((img) => (
        <StyleImageThumbnail
          key={img.style_id}
          src={img.url}
          alt={`${displayName} · ${img.style_code}`}
          size={size}
          emptyText
        />
      ))}
    </div>
  );
}
