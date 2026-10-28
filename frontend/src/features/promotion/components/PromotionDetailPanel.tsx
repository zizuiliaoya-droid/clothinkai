import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
// 主线抽屉「单据详情」页签（流程线设计 8.4）：拉推广单详情（带 ui.fields），按分组从上到下渲染。
//
// - 矩阵里已有的分组（商品归属与颜色尺码、收件、发货）按 ui.fields 出：不在 fields 里整块不渲染（运营看不到收件），
//   edit 的分组右上角出「编辑」，打开列表页现有的弹窗
// - 内容分类与其他平台按矩阵控制编辑；报价读不到时接口给 null，整行不出
// - 不显示商品编码 / SKU 编码（规-1）
import type { ReactNode } from "react";
import { Button, Descriptions, Space, Spin, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { FieldBlock } from "@/features/flow/FieldBlock";
import { shipStatusStyle } from "@/features/flow/stageStyle";
import { goodsDisplayName } from "@/features/product/api";
import { getPromotion, promotionDetailKey } from "@/features/promotion/api";
import { itemSpecLines, shipDetailLines } from "@/features/promotion/shipDisplay";
import type { Promotion } from "@/features/promotion/types";
import { FlowAction } from "@/features/flow/FlowAction";
import type { FreightModalMode } from "./FreightModal";
import { ContentCategoryEditor } from "./ContentCategoryEditor";
import { PlatformPostsEditor } from "./PlatformPostsEditor";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title}>
      <Typography.Text strong>{title}</Typography.Text>
      <div>{children}</div>
    </section>
  );
}

const dash = (v: ReactNode) => (v == null || v === "" ? "—" : v);

export function PromotionDetailPanel({
  promotionId,
  onEditReceiver,
  onEditItems,
  onEditRetro,
  onRecallStart,
  onRecallWaybill,
  onRecallResult,
  onPaymentQr,
  onFreight,
}: {
  promotionId: string;
  /** 打开「改收件信息」弹窗（ui.fields.receiver = edit 时出按钮）。 */
  onEditReceiver?: (row: Promotion) => void;
  /** 打开「改颜色尺码」弹窗（ui.fields.goods_items = edit 时出按钮）。 */
  onEditItems?: (row: Promotion) => void;
  onEditRetro?: (row: Promotion) => void;
  onRecallStart?: (row: Promotion) => void;
  onRecallWaybill?: (row: Promotion) => void;
  onRecallResult?: (row: Promotion) => void;
  onPaymentQr?: (row: Promotion) => void;
  onFreight?: (row: Promotion, mode: FreightModalMode) => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: promotionDetailKey(promotionId),
    queryFn: () => getPromotion(promotionId),
  });
  if (isLoading) return <Spin />;
  if (error || !data) return <Typography.Text type="secondary">单据详情加载失败</Typography.Text>;
  const ui = data.ui;
  const spec = itemSpecLines(data);
  const goods = goodsDisplayName(data.goods_title, data.goods_short_name);
  const ship = data.ship_status ? shipStatusStyle(data.ship_status) : null;
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {data.completed_at && <Tag color="green">已完结 · {data.completed_at.slice(0, 10)}</Tag>}
      <Section title="合作信息">
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="合作模式">{dash(data.cooperation_mode)}</Descriptions.Item>
          <Descriptions.Item label="合作平台">{dash(data.platform)}</Descriptions.Item>
          <Descriptions.Item label="内容分类"><ContentCategoryEditor promotion={data} /></Descriptions.Item>
          <Descriptions.Item label="合作日期">{dash(data.cooperation_date)}</Descriptions.Item>
          <Descriptions.Item label="预定发布日期">{dash(data.scheduled_publish_date)}</Descriptions.Item>
          {data.quote_amount != null && (
            <Descriptions.Item label="报价">¥{data.quote_amount}</Descriptions.Item>
          )}
          {data.cooperation_mode === "寄拍" && (
            <Descriptions.Item label="寄回单号">{dash(data.return_waybill)}</Descriptions.Item>
          )}
        </Descriptions>
      </Section>
      <FieldBlock
        ui={ui}
        group="goods_items"
        title="商品归属与颜色尺码"
        onEdit={onEditItems ? () => onEditItems(data) : undefined}
      >
        <div>
          {dash(goods)}
          {data.goods_is_suit && <Tag color="purple" style={{ marginInlineStart: 4 }}>套装</Tag>}
        </div>
        {spec ? (
          spec.lines.map((line, i) => (
            <div key={i}>
              {line}
              {spec.legacy && i === 0 && <Tag style={{ marginInlineStart: 4 }}>旧</Tag>}
            </div>
          ))
        ) : (
          <Typography.Text type="secondary">颜色尺码未选</Typography.Text>
        )}
      </FieldBlock>
      <FieldBlock
        ui={ui}
        group="receiver"
        title="收件"
        onEdit={onEditReceiver ? () => onEditReceiver(data) : undefined}
      >
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="收件人">{dash(data.receiver_name)}</Descriptions.Item>
          <Descriptions.Item label="电话">{dash(data.receiver_phone)}</Descriptions.Item>
          <Descriptions.Item label="地址">{dash(data.receiver_address)}</Descriptions.Item>
        </Descriptions>
      </FieldBlock>
      <FieldBlock ui={ui} group="shipping" title="发货">
        {ship ? (
          <>
            <Tag color={ship.color}>{ship.label}</Tag>
            {shipDetailLines(data).map((line) => (
              <div key={line}>{line}</div>
            ))}
          </>
        ) : (
          <Typography.Text type="secondary">未进发货流程</Typography.Text>
        )}
      </FieldBlock>
      <Section title="发布">
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="发布状态">{data.publish_status}</Descriptions.Item>
          <Descriptions.Item label="实际发布日期">{dash(data.actual_publish_date)}</Descriptions.Item>
          <Descriptions.Item label="发布链接">
            {/* 只给 http(s) 出链接，别的协议（javascript: 等）按文字显示 */}
            {data.publish_url && /^https?:\/\//i.test(data.publish_url) ? (
              <Typography.Link href={data.publish_url} target="_blank" rel="noreferrer">
                {data.publish_url}
              </Typography.Link>
            ) : (
              dash(data.publish_url)
            )}
          </Descriptions.Item>
        </Descriptions>
      </Section>
      <PlatformPostsEditor promotion={data} />
      <FieldBlock ui={ui} group="payment_qr" title="收款码">
        {data.payment_qr_signed_url ? <StyleImageThumbnail size={100} src={data.payment_qr_signed_url} alt="博主收款码" /> : <Typography.Text>{data.payment_qr_attachment_id ? "已上传收款码（暂时无法预览）" : "尚未上传收款码"}</Typography.Text>}
        {onPaymentQr && <Button onClick={() => onPaymentQr(data)}>{ui?.fields?.payment_qr?.state === "edit" ? "管理收款码" : "查看收款码"}</Button>}
      </FieldBlock>
      <FieldBlock ui={ui} group="recall_info" title="召回">
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="召回状态">{data.recall_status}</Descriptions.Item>
          <Descriptions.Item label="召回原因"><span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{dash(data.recall_reason)}</span></Descriptions.Item>
          {data.return_shipping_fee != null && <Descriptions.Item label="寄回运费">¥{data.return_shipping_fee}</Descriptions.Item>}
          {data.return_shipping_payer && <Descriptions.Item label="承担方">{data.return_shipping_payer}</Descriptions.Item>}
        </Descriptions>
        {data.recall_waybill_signed_url ? <StyleImageThumbnail size={100} src={data.recall_waybill_signed_url} alt="召回寄回截图" /> : data.recall_waybill_attachment_id ? <Typography.Paragraph>已上传寄回截图（暂时无法预览）</Typography.Paragraph> : null}
        <Space wrap>
          {onRecallStart && <FlowAction ui={ui} actionKey="recall_start" label="发起召回" onClick={() => onRecallStart(data)} showReasonBelow />}
          {onRecallWaybill && <FlowAction ui={ui} actionKey="recall_waybill" label="上传召回寄回截图" onClick={() => onRecallWaybill(data)} showReasonBelow />}
          {onRecallResult && <FlowAction ui={ui} actionKey="recall_result" label="登记召回结果" onClick={() => onRecallResult(data)} showReasonBelow />}
        </Space>
      </FieldBlock>
      <FieldBlock ui={ui} group="freight_payment" title="寄回运费">
        {data.freight_payment ? <Space direction="vertical">
          <Tag>{data.freight_payment.status}</Tag>
          {data.freight_payment.amount != null && <Typography.Text>金额 ¥{data.freight_payment.amount}</Typography.Text>}
          {data.freight_payment.reject_reason && <Typography.Paragraph style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>驳回原因：{data.freight_payment.reject_reason}</Typography.Paragraph>}
          {data.freight_payment.payment_date && <Typography.Text>付款日期：{data.freight_payment.payment_date}</Typography.Text>}
          {onFreight && <Space wrap>
            <Button onClick={() => onFreight(data, "view")}>查看运费付款单</Button>
            <FlowAction ui={data.freight_payment.ui} actionKey="freight_review" label="审核寄回运费" onClick={() => onFreight(data, "review")} showReasonBelow />
            <FlowAction ui={data.freight_payment.ui} actionKey="freight_resubmit" label="重提寄回运费" onClick={() => onFreight(data, "resubmit")} showReasonBelow />
            <FlowAction ui={data.freight_payment.ui} actionKey="freight_pay" label="支付寄回运费" onClick={() => onFreight(data, "pay")} showReasonBelow />
            <FlowAction ui={data.freight_payment.ui} actionKey="freight_reject_payment" label="驳回寄回运费付款" onClick={() => onFreight(data, "reject")} showReasonBelow />
          </Space>}
        </Space> : <Typography.Paragraph type="secondary">尚无寄回运费付款单</Typography.Paragraph>}
        {onFreight && <FlowAction ui={ui} actionKey="freight_submit" label="提交寄回运费" onClick={() => onFreight(data, "submit")} showReasonBelow />}
      </FieldBlock>
      <FieldBlock ui={ui} group="retro" title="复盘" onEdit={onEditRetro ? () => onEditRetro(data) : undefined}>
        <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
          {data.retro_content || "主管尚未填写复盘"}
        </Typography.Paragraph>
      </FieldBlock>
    </Space>
  );
}
