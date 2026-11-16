// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { ProductImportToolbar } from "./ProductImportToolbar";
import * as api from "@/features/import/api";
import type { ImportBatch } from "@/features/import/types";
import { useSupportStore } from "@/stores/supportStore";

vi.mock("@/features/import/api", () => ({
  getImportAccess: vi.fn(), getImportMappingSpec: vi.fn(), getImportConflictSummary: vi.fn(),
  uploadImportFile: vi.fn(), downloadImportTemplate: vi.fn(), getImportBatch: vi.fn(), getImportBatchNotes: vi.fn(),
}));

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => getStyle(element);
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getImportAccess).mockResolvedValue([{source: "manual_style_sku", label: "商品资料", configurable: true, can_upload: true, can_map: true, can_view: true, can_resolve: true}]);
  vi.mocked(api.getImportConflictSummary).mockResolvedValue({pending: 0});
  vi.mocked(api.getImportMappingSpec).mockResolvedValue({source: "manual_style_sku", targets: [], builtin_columns: [], active: null});
  vi.mocked(api.getImportBatchNotes).mockResolvedValue({items: [], total: 0, page: 1, page_size: 10});
});
afterEach(() => { cleanup(); useSupportStore.getState().end(); });

function show() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  const view = render(<MemoryRouter><QueryClientProvider client={client}><ProductImportToolbar /></QueryClientProvider></MemoryRouter>);
  return {client, ...view};
}

it("上传和映射仍按来源授权，普通只读角色没有商品导入入口", async () => {
  vi.mocked(api.getImportAccess).mockResolvedValue([{source: "manual_style_sku", label: "商品资料", configurable: true, can_upload: false, can_map: false, can_view: true, can_resolve: false}]);
  show();
  await waitFor(() => expect(api.getImportConflictSummary).toHaveBeenCalled());
  expect(screen.queryByRole("button", {name: /导入商品资料/})).toBeNull();
  expect(screen.queryByRole("button", {name: /下载空模板/})).toBeNull();
  expect(screen.queryByRole("button", {name: "字段映射"})).toBeNull();
  expect(api.getImportMappingSpec).not.toHaveBeenCalled();
});

it("只读排查即使接口返回写能力也隐藏上传和映射", async () => {
  useSupportStore.getState().start({access_token: "mock", expires_in: 900, tenant: {id: "t", code: "t", name: "验收"}});
  show();
  await waitFor(() => expect(api.getImportConflictSummary).toHaveBeenCalled());
  expect(screen.queryByRole("button", {name: /导入商品资料/})).toBeNull();
  expect(screen.queryByRole("button", {name: "字段映射"})).toBeNull();
});

it("异步解析完成后刷新商品、款式、规格和成本，不能只在上传时刷新", async () => {
  const batch = {id: "batch1", source: "manual_style_sku", original_filename: "商品.xlsx", status: "processing", total_rows: 1, imported: 0, failed: 0, filled: 0, skipped: 0, conflicted: 0, warning_count: 0, filled_objects: 0, pending_conflicts: 0} as ImportBatch;
  vi.mocked(api.uploadImportFile).mockResolvedValue({batch_id: batch.id, source: batch.source, status: "processing"});
  vi.mocked(api.getImportBatch).mockResolvedValue(batch);
  const {container, client} = show();
  await screen.findByRole("button", {name: /导入商品资料/});
  fireEvent.change(container.querySelector('input[type="file"]')!, {target: {files: [new File(["data"], "商品.xlsx")]}});
  await screen.findByText(/正在处理「商品.xlsx」/);
  expect(api.uploadImportFile).toHaveBeenCalledWith("manual_style_sku", expect.any(File));
  const keys = ["goods", "styles", "skus", "cost-table"];
  for (const key of keys) client.setQueryData([key], []);
  for (const key of keys) expect(client.getQueryState([key])?.isInvalidated).toBe(false);
  act(() => {client.setQueryData(["import-batches", "detail", batch.id], {...batch, status: "completed", imported: 1});});
  await waitFor(() => {for (const key of keys) expect(client.getQueryState([key])?.isInvalidated).toBe(true);});
});
