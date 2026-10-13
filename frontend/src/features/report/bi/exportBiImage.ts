// BI 导出图片（7e R6 S10，设计 2.6）：把当前 Tab 容器用 html-to-image 截成 PNG。
// 截图前往节点里插一层水印（铺满、重复文字、斜 30°、透明度 12%、不挡鼠标），截完在 finally 里移除；
// 水印文字 =「登录名 姓名 YYYY-MM-DD HH:mm」（系统没有工号字段，用登录名代替，偏差 5）。
import { useCallback, useState } from "react";
import { message } from "antd";
import dayjs from "dayjs";
import { toPng } from "html-to-image";
import { useAuthStore } from "@/stores/authStore";

export type BiImageTab = "store" | "goods" | "personal";

/** 水印层的标记属性，测试与移除都靠它找。 */
export const WATERMARK_ATTR = "data-bi-watermark";

/** 水印文字：登录名 + 姓名（没填姓名用登录名）+ 导出时间到分钟。 */
export function watermarkText(
  user: { username: string; display_name: string | null } | null,
  now: Date
): string {
  const username = user?.username ?? "";
  const name = user?.display_name || username;
  return `${username} ${name} ${dayjs(now).format("YYYY-MM-DD HH:mm")}`;
}

/** 文件名 bi-<tab>_<起>_<止>.png，起止用接口返回的实际区间。 */
export function biImageFilename(tab: BiImageTab, dateFrom: string, dateTo: string): string {
  return `bi-${tab}_${dateFrom}_${dateTo}.png`;
}

// 每格水印的大小（px）：文字斜着排，格子留宽一些免得相互压字
const CELL_W = 260;
const CELL_H = 140;

function buildWatermark(node: HTMLElement, text: string): HTMLDivElement {
  const layer = document.createElement("div");
  layer.setAttribute(WATERMARK_ATTR, "");
  layer.setAttribute("aria-hidden", "true");
  Object.assign(layer.style, {
    position: "absolute",
    inset: "0",
    overflow: "hidden",
    pointerEvents: "none",
    opacity: "0.12",
    zIndex: "10",
    display: "flex",
    flexWrap: "wrap",
    alignContent: "flex-start",
  });
  // 按节点实际大小铺满；jsdom 下尺寸为 0，至少放一格
  const cols = Math.max(1, Math.ceil(node.scrollWidth / CELL_W) + 1);
  const rows = Math.max(1, Math.ceil(node.scrollHeight / CELL_H) + 1);
  for (let i = 0; i < cols * rows; i += 1) {
    const cell = document.createElement("span");
    cell.textContent = text;
    Object.assign(cell.style, {
      width: `${CELL_W}px`,
      height: `${CELL_H}px`,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      whiteSpace: "nowrap",
      transform: "rotate(-30deg)",
      color: "#0f172a",
      fontSize: "14px",
    });
    layer.appendChild(cell);
  }
  return layer;
}

/**
 * 插水印 → toPng → 下载；无论成败都移除水印、还原节点的 position。
 * 失败抛出，由调用方统一提示。
 */
export async function exportBiImage(node: HTMLElement, text: string, filename: string): Promise<void> {
  const prevPosition = node.style.position;
  // 水印层绝对定位铺满节点，节点是 static 时临时改成 relative
  const computed = window.getComputedStyle(node).position;
  if (!computed || computed === "static") node.style.position = "relative";
  const layer = buildWatermark(node, text);
  node.appendChild(layer);
  try {
    const dataUrl = await toPng(node, { pixelRatio: 2, backgroundColor: "#fff" });
    const link = document.createElement("a");
    link.href = dataUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    layer.remove();
    node.style.position = prevPosition;
  }
}

/** 页面用：取当前登录人生成水印，失败只提示「导出失败，请重试」。 */
export function useBiImageExport() {
  const user = useAuthStore((s) => s.user);
  const [exporting, setExporting] = useState(false);
  const run = useCallback(
    async (node: HTMLElement | null, filename: string) => {
      if (!node) return;
      setExporting(true);
      try {
        await exportBiImage(node, watermarkText(user, new Date()), filename);
      } catch {
        message.error("导出失败，请重试");
      } finally {
        setExporting(false);
      }
    },
    [user]
  );
  return { exporting, run };
}
