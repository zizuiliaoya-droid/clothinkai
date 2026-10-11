import { Spin, Typography, theme } from "antd";
import {
  NO_OPINION_TEXT,
  NO_REJECTIONS_TEXT,
  rejectionMetaLine,
  rejectionOpinion,
} from "@/features/negotiation/bloggerRejections";
import type { BloggerCooperationHistory } from "@/features/negotiation/types";

/** 驳回意见两行省略（视觉上），全文仍在 DOM 里给读屏；悬停看全文。 */
const TWO_LINES = {
  display: "-webkit-box",
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: 2,
  overflow: "hidden",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
} as const;

/**
 * 博主 hover 卡「谈款驳回记录」（流程线设计 8.7、7c-7）：所有 PR 的主管驳回单（老板退回不进），
 * 最多 5 条，标题旁「共 N 次」。数据来自同一个 history 接口，不另发请求。
 */
export function NegotiationRejections({
  data,
  loading,
}: {
  data: Pick<BloggerCooperationHistory, "rejections" | "total_rejections"> | undefined;
  loading: boolean;
}) {
  const { token } = theme.useToken();
  // 说明行用 colorTextSecondary（Typography 的 secondary 对比度不够 4.5:1）
  const muted = { color: token.colorTextSecondary, fontSize: token.fontSizeSM };
  const rows = data?.rejections ?? [];
  return (
    <section aria-label="谈款驳回记录">
      <Typography.Text strong>谈款驳回记录</Typography.Text>
      {(data?.total_rejections ?? 0) > 0 && (
        <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
          共 {data?.total_rejections} 次
        </Typography.Text>
      )}
      <div style={{ marginTop: 8 }}>
        {loading ? (
          <Spin style={{ padding: 16 }} />
        ) : rows.length === 0 ? (
          <Typography.Text type="secondary">{NO_REJECTIONS_TEXT}</Typography.Text>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {rows.map((r, i) => {
              const opinion = rejectionOpinion(r);
              return (
                <li
                  key={r.negotiation_id ?? `${r.rejected_at}-${i}`}
                  style={{ padding: "4px 0", borderTop: i === 0 ? undefined : "1px solid #f0f0f0" }}
                >
                  <div style={muted}>{rejectionMetaLine(r)}</div>
                  {opinion ? (
                    <div title={opinion} style={TWO_LINES}>
                      {opinion}
                    </div>
                  ) : (
                    <div style={{ color: token.colorTextSecondary }}>{NO_OPINION_TEXT}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
