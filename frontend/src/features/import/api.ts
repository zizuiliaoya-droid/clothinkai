// U06a 统一导入框架 feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  ConflictResolveRequest,
  ConflictResolveResponse,
  FieldMapping,
  FieldMappingCreate,
  ImportBatch,
  ImportBatchListFilters,
  ImportBatchPage,
  ImportConflictFilters,
  ImportConflictPage,
  ImportJobNotesPage,
  ImportSourceAccess,
  ImportUploadResponse,
} from "./types";

/**
 * 当前用户对每个已注册导入来源的能力（看 / 上传 / 改映射 / 裁决）。
 * 导入按钮按它显示，不在前端写死角色（商品资料只给管理员、跟单、运营）。
 */
export async function getImportAccess(): Promise<ImportSourceAccess[]> {
  const resp = await apiClient.get<ImportSourceAccess[]>("/api/imports/access");
  return resp.data;
}

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

/** 批次里有提示或补空的行（行号、类别、提示、补空字段名；不含任何值）。 */
export async function getImportBatchNotes(
  batchId: string,
  params: { page?: number; page_size?: number } = {}
): Promise<ImportJobNotesPage> {
  const resp = await apiClient.get<ImportJobNotesPage>(
    `/api/imports/batches/${batchId}/notes`,
    { params }
  );
  return resp.data;
}

// 导入冲突（8a-6）

/** 冲突列表：只含可见来源；受保护字段对没有读权限的人 masked。 */
export async function listImportConflicts(
  filters: ImportConflictFilters = {}
): Promise<ImportConflictPage> {
  const resp = await apiClient.get<ImportConflictPage>("/api/imports/conflicts", {
    params: filters,
  });
  return resp.data;
}

/** 某来源仍待处理的冲突条数。 */
export async function getImportConflictSummary(
  source: string
): Promise<{ pending: number }> {
  const resp = await apiClient.get<{ pending: number }>(
    "/api/imports/conflicts/summary",
    { params: { source } }
  );
  return resp.data;
}

/** 下载冲突明细 CSV（同列表筛选；超过 10,000 条后端返回 422）。 */
export async function downloadImportConflicts(
  filters: Omit<ImportConflictFilters, "page" | "page_size"> = {}
): Promise<Blob> {
  const resp = await apiClient.get("/api/imports/conflicts/download", {
    params: filters,
    responseType: "blob",
    timeout: 120_000,
  });
  return resp.data as Blob;
}

/**
 * 裁决冲突（单条与多选同一接口，1 ~ 200 条）。权限整单预检（403 零改动），
 * 之后逐条处理、逐条返回结果（stale 带当前值，需确认后带新的期望值重发）。
 */
export async function resolveImportConflicts(
  payload: ConflictResolveRequest
): Promise<ConflictResolveResponse> {
  const resp = await apiClient.post<ConflictResolveResponse>(
    "/api/imports/conflicts/resolve",
    payload,
    { timeout: 120_000 }
  );
  return resp.data;
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
