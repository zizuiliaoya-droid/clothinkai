// 仓库页接口（流程线设计 7.4）：仓库 060 起没有 promotion:read，只走这三个端点。
import { apiClient } from "@/services/apiClient";
import type {
  WarehouseShipmentFilters,
  WarehouseShipmentPage,
  WarehouseShipmentRow,
  WarehouseWaybillRequest,
} from "./types";

/** 仓库页列表（promotion_ship:fill）。 */
export async function listWarehouseShipments(
  filters: WarehouseShipmentFilters
): Promise<WarehouseShipmentPage> {
  const resp = await apiClient.get<WarehouseShipmentPage>("/api/warehouse/shipments", {
    params: filters,
  });
  return resp.data;
}

/** 回填 / 改快递信息（promotion_ship:fill）：只回仓库行投影。 */
export async function updateWarehouseWaybill(
  promotionId: string,
  body: WarehouseWaybillRequest
): Promise<WarehouseShipmentRow> {
  const resp = await apiClient.patch<WarehouseShipmentRow>(
    `/api/promotions/${promotionId}/warehouse-waybill`,
    body
  );
  return resp.data;
}

/** 导出 xlsx（promotion_ship:export），参数同列表；失败时错误体是 Blob，交给 exportErrorMessage 读。 */
export async function exportWarehouseShipments(
  filters: Pick<WarehouseShipmentFilters, "bucket" | "keyword">
): Promise<Blob> {
  const resp = await apiClient.get<Blob>("/api/warehouse/shipments/export", {
    params: filters,
    responseType: "blob",
    timeout: 120_000,
  });
  return resp.data;
}
