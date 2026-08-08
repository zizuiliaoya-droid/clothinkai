const TARGET_BYTES = 280 * 1024;
const MAX_BYTES = 300 * 1024;
const MAX_EDGE = 1600;
const QUALITY_STEPS = [0.86, 0.78, 0.7, 0.62, 0.54, 0.46, 0.38];
const SIZE_STEPS = [1, 0.88, 0.76, 0.66, 0.56];
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

export interface CompressedStyleImage {
  file: File;
  originalBytes: number;
  compressedBytes: number;
  width: number;
  height: number;
  format: "image/webp" | "image/jpeg";
}

type DecodedImage = {
  source: CanvasImageSource;
  width: number;
  height: number;
  dispose: () => void;
};

async function decodeImage(file: File): Promise<DecodedImage> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    return {
      source: bitmap,
      width: bitmap.width,
      height: bitmap.height,
      dispose: () => bitmap.close(),
    };
  }

  const url = URL.createObjectURL(file);
  const image = new Image();
  image.decoding = "async";
  try {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("图片解码失败，请更换文件后重试"));
      image.src = url;
    });
    return {
      source: image,
      width: image.naturalWidth,
      height: image.naturalHeight,
      dispose: () => URL.revokeObjectURL(url),
    };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}

function canvasToBlob(
  canvas: HTMLCanvasElement,
  type: "image/webp" | "image/jpeg",
  quality: number
): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

function drawImage(
  decoded: DecodedImage,
  width: number,
  height: number,
  type: "image/webp" | "image/jpeg"
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: type !== "image/jpeg" });
  if (!context) throw new Error("浏览器无法创建图片压缩画布");
  if (type === "image/jpeg") {
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
  }
  context.drawImage(decoded.source, 0, 0, width, height);
  return canvas;
}

async function compressAs(
  decoded: DecodedImage,
  baseWidth: number,
  baseHeight: number,
  type: "image/webp" | "image/jpeg"
): Promise<{ blob: Blob; width: number; height: number } | null> {
  let best: { blob: Blob; width: number; height: number } | null = null;
  for (const sizeScale of SIZE_STEPS) {
    const width = Math.max(1, Math.round(baseWidth * sizeScale));
    const height = Math.max(1, Math.round(baseHeight * sizeScale));
    const canvas = drawImage(decoded, width, height, type);
    for (const quality of QUALITY_STEPS) {
      const blob = await canvasToBlob(canvas, type, quality);
      if (!blob || blob.type !== type) break;
      if (!best || blob.size < best.blob.size) best = { blob, width, height };
      if (blob.size <= TARGET_BYTES) return { blob, width, height };
    }
  }
  return best && best.blob.size < MAX_BYTES ? best : null;
}

function outputName(name: string, type: "image/webp" | "image/jpeg"): string {
  const base = name.replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_") || "style-main";
  return `${base}.${type === "image/webp" ? "webp" : "jpg"}`;
}

export function formatImageKilobytes(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)}KB`;
}

export async function compressStyleMainImage(
  sourceFile: File
): Promise<CompressedStyleImage> {
  if (!ALLOWED_TYPES.has(sourceFile.type)) {
    throw new Error("主图仅支持 JPG、PNG、WebP 图片");
  }
  if (!sourceFile.size) throw new Error("主图文件不能为空");

  const decoded = await decodeImage(sourceFile);
  try {
    if (!decoded.width || !decoded.height) throw new Error("无法读取图片尺寸");
    const initialScale = Math.min(1, MAX_EDGE / Math.max(decoded.width, decoded.height));
    const width = Math.max(1, Math.round(decoded.width * initialScale));
    const height = Math.max(1, Math.round(decoded.height * initialScale));

    const webp = await compressAs(decoded, width, height, "image/webp");
    const compressed = webp ?? (await compressAs(decoded, width, height, "image/jpeg"));
    if (!compressed || compressed.blob.size >= MAX_BYTES) {
      throw new Error("图片压缩后仍达到 300KB，请选择内容更简单或尺寸更小的图片");
    }
    const type = compressed.blob.type as "image/webp" | "image/jpeg";
    return {
      file: new File([compressed.blob], outputName(sourceFile.name, type), {
        type,
        lastModified: Date.now(),
      }),
      originalBytes: sourceFile.size,
      compressedBytes: compressed.blob.size,
      width: compressed.width,
      height: compressed.height,
      format: type,
    };
  } finally {
    decoded.dispose();
  }
}