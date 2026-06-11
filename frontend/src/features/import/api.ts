// U06a 统一导入框架 feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  FieldMapping,
  FieldMappingCreate,
  ImportBatch,
  ImportBatchListFilters,
  ImportBatchPage,
  ImportUploadResponse,
} from "./types";

/**
 * 上传导入文件（multipart）。
 * 后端 DB 先行 + UNIQUE 去重，重复文件返回 409（IMPORT_DUPLICATE_FILE）。
 */
export async function uploadImportFile(
  source: string,
  file: File,
  mappingVersion?: number
): Promise<ImportUploadResponse> {
  const form = new FormData();
  form.append("source", source);
  form.append("file", file);
  if (mappingVersion != null) {
    form.append("mapping_version", String(mappingVersion));
  }
  const resp = await apiClient.post<ImportUploadResponse>(
    "/api/imports/upload",
    form,
    { headers: { "Content-Type": "multipart/form-data" } }
  );
  return resp.data;
}

export async function listImportBatches(
  filters: ImportBatchListFilters = {}
): Promise<ImportBatchPage> {
  const resp = await apiClient.get<ImportBatchPage>("/api/imports/batches", {
    params: filters,
  });
  return resp.data;
}

export async function getImportBatch(batchId: string): Promise<ImportBatch> {
  const resp = await apiClient.get<ImportBatch>(
    `/api/imports/batches/${batchId}`
  );
  return resp.data;
}

/**
 * 重试批次。仅 partial / failed 可重试（retry_count<3）。
 * 409：重试次数耗尽（IMPORT_RETRY_EXHAUSTED）或正在处理中（IMPORT_BATCH_BUSY）。
 */
export async function retryImportBatch(batchId: string): Promise<ImportBatch> {
  const resp = await apiClient.post<ImportBatch>(
    `/api/imports/batches/${batchId}/retry`
  );
  return resp.data;
}

/**
 * 下载失败明细 CSV（带 csv_safe 注入防护 + UTF-8 BOM）。
 * 返回 Blob 供浏览器另存。
 */
export async function downloadImportErrors(batchId: string): Promise<Blob> {
  const resp = await apiClient.get(
    `/api/imports/batches/${batchId}/errors/download`,
    { responseType: "blob" }
  );
  return resp.data as Blob;
}

// 字段映射版本

export async function createFieldMapping(
  payload: FieldMappingCreate
): Promise<FieldMapping> {
  const resp = await apiClient.post<FieldMapping>(
    "/api/imports/field-mappings",
    payload
  );
  return resp.data;
}

export async function listFieldMappings(
  source: string
): Promise<FieldMapping[]> {
  const resp = await apiClient.get<FieldMapping[]>(
    "/api/imports/field-mappings",
    { params: { source } }
  );
  return resp.data;
}

export async function getActiveFieldMapping(
  source: string
): Promise<FieldMapping | null> {
  const resp = await apiClient.get<FieldMapping | null>(
    "/api/imports/field-mappings/active",
    { params: { source } }
  );
  return resp.data;
}
