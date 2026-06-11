// design feature API + 类型（对齐后端 design schemas）。

import { apiClient } from "@/services/apiClient";

export interface DesignListItem {
  id: string;
  style_code: string;
  style_name: string;
  design_status: string;
  main_image_key: string | null;
}

export interface DesignStatusGroup {
  status: string;
  count: number;
  items: DesignListItem[];
}

export interface DesignListResponse {
  groups: DesignStatusGroup[];
  total: number;
}

export async function listDesigns(): Promise<DesignListResponse> {
  const resp = await apiClient.get<DesignListResponse>("/api/designs/");
  return resp.data;
}
