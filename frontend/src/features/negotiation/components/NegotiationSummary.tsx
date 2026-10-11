// 审核弹窗顶部的谈款摘要：博主、商品（简称 + 每个成员的颜色尺码）、模式、平台、约定发布、服务费、对接 PR。不显示编码（规-1）。
import { Descriptions, Tag } from "antd";
import { negotiationItemLines } from "../negotiationList";
import type { Negotiation } from "../types";

type Props = { row: Negotiation };

export function NegotiationSummary({ row }: Props) {
  const lines = negotiationItemLines(row);
  return (
    <Descriptions size="small" column={1} bordered style={{ marginTop: 16 }}>
      <Descriptions.Item label="博主">{row.blogger_nickname ?? "—"}</Descriptions.Item>
      <Descriptions.Item label="商品">
        <span title={row.goods_title ?? undefined}>{row.display_short_name ?? row.style_name ?? "—"}</span>
        {row.goods_is_suit && (
          <Tag color="purple" style={{ marginInlineStart: 8 }}>
            套装
          </Tag>
        )}
        {lines.map((line, i) => (
          <div key={i}>{line}</div>
        ))}
      </Descriptions.Item>
      <Descriptions.Item label="合作模式">{row.cooperation_mode}</Descriptions.Item>
      <Descriptions.Item label="平台">{row.platform}</Descriptions.Item>
      <Descriptions.Item label="约定发布">{row.scheduled_publish_date ?? "—"}</Descriptions.Item>
      {row.quote_amount != null && (
        <Descriptions.Item label="服务费">¥{Number(row.quote_amount).toFixed(2)}</Descriptions.Item>
      )}
      <Descriptions.Item label="对接PR">{row.pr_name ?? "—"}</Descriptions.Item>
      {row.remark && <Descriptions.Item label="备注">{row.remark}</Descriptions.Item>}
    </Descriptions>
  );
}
