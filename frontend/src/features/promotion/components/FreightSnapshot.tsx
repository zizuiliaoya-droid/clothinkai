import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { Descriptions, Space, Tag, Typography } from "antd";
import type { ReturnFreightPayment } from "../freightTypes";

export function FreightSnapshot({ data }: { data: ReturnFreightPayment }) {
  return <Space direction="vertical" style={{ width: "100%" }}>
    <Descriptions column={1} size="small">
      <Descriptions.Item label="状态"><Tag>{data.status}</Tag></Descriptions.Item>
      {data.amount !== null && <Descriptions.Item label="金额">¥{data.amount}</Descriptions.Item>}
      <Descriptions.Item label="提交人 / 时间">{data.submitted_by_name ?? "—"} / {data.submitted_at?.replace("T", " ").slice(0, 16)}</Descriptions.Item>
      {data.reviewed_by_name && <Descriptions.Item label="审核人">{data.reviewed_by_name}</Descriptions.Item>}
      {data.reject_reason && <Descriptions.Item label="驳回原因"><span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{data.reject_reason}</span></Descriptions.Item>}
      {data.payment_date && <Descriptions.Item label="付款日期">{data.payment_date}</Descriptions.Item>}
    </Descriptions>
    <Space wrap align="start">
      {data.payment_qr_signed_url && <div><Typography.Text>收款码快照</Typography.Text><div><StyleImageThumbnail size={100} src={data.payment_qr_signed_url} alt="收款码快照" /></div></div>}
      {data.waybill_signed_url && <div><Typography.Text>寄回截图快照</Typography.Text><div><StyleImageThumbnail size={100} src={data.waybill_signed_url} alt="寄回截图快照" /></div></div>}
      {data.payment_proof_signed_url && <div><Typography.Text>付款凭证</Typography.Text><div><StyleImageThumbnail size={100} src={data.payment_proof_signed_url} alt="寄回运费付款凭证" /></div></div>}
    </Space>
    {(data.payment_qr_attachment_id && !data.payment_qr_signed_url || data.waybill_attachment_id && !data.waybill_signed_url || data.payment_proof_attachment_id && !data.payment_proof_signed_url) && <Typography.Text type="secondary">部分附件已上传，暂时无法预览</Typography.Text>}
  </Space>;
}
