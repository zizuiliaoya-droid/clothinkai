import type { UiState } from "@/features/flow/keys";

export type FreightStatus = "待审核" | "待付款" | "已付款" | "已驳回";

/** R33 独立付款单；附件为提交时快照，不能以推广当前附件替代。 */
export interface ReturnFreightPayment {
  id: string;
  promotion_id: string;
  line_id: string;
  promotion_internal_code: string;
  blogger_nickname: string;
  display_short_name: string | null;
  amount: string | null;
  status: FreightStatus;
  payment_qr_attachment_id: string | null;
  waybill_attachment_id: string | null;
  payment_proof_attachment_id: string | null;
  payment_qr_signed_url: string | null;
  waybill_signed_url: string | null;
  payment_proof_signed_url: string | null;
  submitted_by: string | null;
  submitted_by_name: string | null;
  submitted_at: string;
  reviewed_by: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  rejected_by: string | null;
  rejected_by_name: string | null;
  rejected_at: string | null;
  reject_reason: string | null;
  paid_by: string | null;
  paid_by_name: string | null;
  paid_at: string | null;
  payment_date: string | null;
  remark: string | null;
  ui: UiState;
}

export interface FreightFilters {
  status?: FreightStatus;
  promotion_id?: string;
  keyword?: string;
  payment_date_from?: string;
  payment_date_to?: string;
  page?: number;
  page_size?: number;
}

export interface FreightPage {
  items: ReturnFreightPayment[];
  total: number;
  page: number;
  page_size: number;
}
