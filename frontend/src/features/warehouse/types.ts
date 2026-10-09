// 仓库页（流程线设计 7.4、8.5）。照抄后端 promotion/schemas.py 的 WarehouseShipment*：
// 仓库从任何接口都拿不到整张推广单，这里只有对单、发货要用的字段（没有博主、金额、发布链接、source_extra）。

import type { UiState } from "@/features/flow/keys";

/** 分桶：待打单（默认，先推先打）/ 已发货（发货时间倒序）/ 全部（= 前两者，不含待发货与历史单）。 */
export type WarehouseBucket = "待打单" | "已发货" | "全部";

export const WAREHOUSE_BUCKETS: readonly WarehouseBucket[] = ["待打单", "已发货", "全部"];

/** 仓库行里的一个商品明细：只有品名与颜色尺码（编码只进导出）。 */
export interface WarehouseShipmentItem {
  display_short_name: string;
  color: string;
  size: string;
}

export interface WarehouseShipmentRow {
  id: string;
  internal_code: string;
  /** 货号（款式编码，7a 的 D13 保留）。 */
  style_code: string;
  display_short_name: string;
  goods_title: string | null;
  items: WarehouseShipmentItem[];
  /** 没有明细的旧单：录入信息里的颜色及规格原文。 */
  legacy_color_spec: string | null;
  /** 收件三项过字段规则：读不到的是 null。 */
  receiver_name: string | null;
  receiver_phone: string | null;
  receiver_address: string | null;
  /** 推送后改过地址 / 明细（事件表随 PR-4，这之前恒为 false）。 */
  receiver_updated_after_push: boolean;
  items_updated_after_push: boolean;
  ship_status: "待打单" | "已发货";
  ship_pushed_at: string | null;
  ship_pushed_by_name: string | null;
  ship_courier: string | null;
  ship_waybill: string | null;
  shipped_at: string | null;
  /** 只有 actions.ship_fill（回填 / 改单号）。 */
  ui: Pick<UiState, "actions">;
}

export interface WarehouseShipmentPage {
  items: WarehouseShipmentRow[];
  total: number;
  page: number;
  page_size: number;
  /** 快递公司枚举（后端 ShipCourier），回填弹窗的选项。 */
  couriers: string[];
  /** 页级动作：持 promotion_ship:export 才有 actions.export。 */
  ui: Pick<UiState, "actions">;
}

export interface WarehouseShipmentFilters {
  bucket?: WarehouseBucket;
  keyword?: string;
  page?: number;
  page_size?: number;
}

/** 回填 / 改快递信息。shipped_at 不传 = 服务器的现在；传就要带时区（ISO 字符串）。 */
export interface WarehouseWaybillRequest {
  courier: string;
  waybill: string;
  shipped_at?: string;
}
