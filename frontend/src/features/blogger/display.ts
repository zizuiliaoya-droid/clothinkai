// 8b 博主页展示用的纯函数。

// §3.8 / r1 N6：渲染前再判一次 scheme；后端 normalize_external_image_url 不区分大小写，这里也不区分
const SAFE_HOMEPAGE = /^https?:\/\//i;

/** 主页链接能渲染成 <a> 时返回去空白后的链接，否则 null（只显示 —）。 */
export function safeHomepageUrl(url: string | null | undefined): string | null {
  const s = url?.trim();
  return s && SAFE_HOMEPAGE.test(s) ? s : null;
}

/**
 * r1 N13：/missing 给的行号是数据行序号（从表头下一行数起），不是 Excel 行号；
 * 写明换算，免得照着复制差一行。后端只给前 20 个行号，次数更多时末尾带「等」。
 */
export function missingTagRowsText(rows: number[], count: number): string {
  if (!rows.length) return "";
  const more = count > rows.length ? "等" : "";
  return `数据第 ${rows.join("、")} 行${more}（Excel 行号 = 数据行号 + 表头所在行号，表头在第 1 行就加 1）`;
}
