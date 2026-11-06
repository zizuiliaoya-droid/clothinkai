// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {afterEach, beforeAll, beforeEach, expect, it, vi} from "vitest";
import {message} from "antd";
import {BulkCreateModal} from "./BulkCreateModal";
import type {BulkCreateItem, BulkResult} from "../bulkApi";
import type {NegotiationFormTarget} from "./NegotiationFormModal";
import {negotiationPayload} from "../negotiationForm";

const api = vi.hoisted(() => ({bulkCreate: vi.fn(), lookupBulkCreates: vi.fn(), listGoodsForStyle: vi.fn()}));
vi.mock("../bulkApi", async original => ({...await original<typeof import("../bulkApi")>(), ...api}));
vi.mock("./useNegotiationFeedback", () => ({useNegotiationFeedback: () => ({refresh: vi.fn()})}));
vi.mock("@/features/product/api", () => ({listStyles: () => Promise.resolve({items: [{id: "s1", style_code: "S1", style_name: "裙子"}, {id: "s2", style_code: "S2", style_name: "上衣"}]}), listGoodsForStyle: api.listGoodsForStyle}));
vi.mock("../api", () => ({negotiationGoodsMembers: (id: string, goods: string) => Promise.resolve({goods_main_id: goods, members: [{style_id: id, display_short_name: id, goods_title: id}]})}));
vi.mock("@/components/RemoteSelect/BloggerSelect", () => ({BloggerSelect: ({id, onChange, value}: {id?: string; onChange: (id: string, option: {label: string}) => void; value?: string}) => <select id={id} value={value ?? ""} onChange={e => onChange(e.target.value, {label: "博主甲"})}><option value="">请选择</option><option value="b1">博主甲</option></select>}));
vi.mock("./AccountSelection", () => ({AccountSelection: () => <span>复用账号组合</span>}));
vi.mock("./NegotiationFormModal", () => ({NegotiationFormModal: ({target, onPrepare, onClose}: {target: NegotiationFormTarget | null; onPrepare: (payload: ReturnType<typeof negotiationPayload>, values: object, members: unknown[]) => void; onClose: () => void}) => target?.prepared ? <button onClick={() => {const values = {...target.prepared!.values, quote_amount: 0}; onPrepare(negotiationPayload(values, target.prepared!.members), values, target.prepared!.members); onClose();}}>此行明确0元</button> : null}));
beforeAll(() => {const original = window.getComputedStyle.bind(window); window.getComputedStyle = element => original(element); Object.defineProperty(window, "matchMedia", {value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});});
beforeEach(() => {vi.clearAllMocks(); api.listGoodsForStyle.mockImplementation((id: string) => Promise.resolve([{goods_main_id: `g-${id}`, goods_title: id, is_suit: false}]));});
afterEach(() => {cleanup(); message.destroy();});
function result(item: BulkCreateItem, status: BulkResult["status"] = "success", code = "CREATED"): BulkResult {
  return {row_key: item.client_create_key, negotiation_id: status === "success" ? `id-${item.payload.style_id}` : null, status, code, message: status === "success" ? "草稿已保存" : "请修改资料", reused: false, negotiation: null};
}
async function showRows(two = true, defaultDate?: string) {
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><BulkCreateModal open onClose={() => {}} onEdit={() => {}} /></QueryClientProvider>);
  fireEvent.change(screen.getByLabelText("本组默认博主"), {target: {value: "b1"}});
  if (defaultDate) {
    const date = screen.getByLabelText("默认约定发布日");
    fireEvent.change(date, {target: {value: defaultDate}}); fireEvent.keyDown(date, {key: "Enter", code: "Enter"}); fireEvent.blur(date);
  }
  fireEvent.mouseDown(screen.getByRole("combobox", {name: "多选款式"}));
  fireEvent.click(await screen.findByText("S1 裙子"));
  if (two) fireEvent.click(screen.getByText("S2 上衣"));
  fireEvent.click(screen.getByRole("button", {name: "生成逐行草稿"}));
  await waitFor(() => expect(screen.getAllByRole("button", {name: "编辑此行"})).toHaveLength(two ? 2 : 1));
}
it("金额逐行未知与明确0分开，部分成功后只重试失败行并保留成功ID", async () => {
  await showRows();
  fireEvent.click(screen.getAllByRole("button", {name: "编辑此行"})[1]);
  fireEvent.click(screen.getByRole("button", {name: "此行明确0元"}));
  api.bulkCreate.mockImplementationOnce((_group: string, items: BulkCreateItem[]) => Promise.resolve({results: [result(items[0]), result(items[1], "failed", "INVALID_ITEM")]}));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await screen.findByText("失败：请修改资料");
  const first = api.bulkCreate.mock.calls[0][1] as BulkCreateItem[];
  expect(first[0].payload).not.toHaveProperty("quote_amount");
  expect(first[1].payload.quote_amount).toBe("0");
  api.bulkCreate.mockImplementationOnce((_group: string, items: BulkCreateItem[]) => Promise.resolve({results: items.map(item => result(item))}));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await waitFor(() => expect(api.bulkCreate).toHaveBeenCalledTimes(2));
  expect(api.bulkCreate.mock.calls[1][0]).toBe(api.bulkCreate.mock.calls[0][0]);
  expect(api.bulkCreate.mock.calls[1][1]).toEqual([first[1]]);
  expect(screen.getByText("单据 ID：id-s1")).toBeInTheDocument();
});
it("响应丢失先lookup，已建行不重发、未建行按原行键原请求重试", async () => {
  await showRows();
  api.bulkCreate.mockRejectedValueOnce(new Error("连接中断"));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await screen.findByText(/结果暂未核实/);
  expect(screen.getAllByRole("button", {name: "编辑此行"})[0]).toBeDisabled();
  const first = api.bulkCreate.mock.calls[0][1] as BulkCreateItem[];
  api.lookupBulkCreates.mockResolvedValue({results: [{...result(first[0]), reused: true}, result(first[1], "skipped", "NOT_CREATED")]});
  api.bulkCreate.mockImplementationOnce((_group: string, items: BulkCreateItem[]) => Promise.resolve({results: items.map(item => result(item))}));
  fireEvent.click(screen.getByRole("button", {name: "核对并重试待处理行"}));
  await waitFor(() => expect(api.bulkCreate).toHaveBeenCalledTimes(2));
  expect(api.lookupBulkCreates).toHaveBeenCalledWith(first.map(item => item.client_create_key));
  expect(api.lookupBulkCreates.mock.invocationCallOrder[0]).toBeLessThan(api.bulkCreate.mock.invocationCallOrder[1]);
  expect(api.bulkCreate.mock.calls[1][1]).toEqual([first[1]]);
  expect(await screen.findByText("成功（已存在，未重复创建）：草稿已保存")).toBeInTheDocument();
});
it("lookup失败保留待核对，不能修改或再次创建", async () => {
  await showRows(false);
  api.bulkCreate.mockRejectedValueOnce(new Error("连接中断"));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await screen.findByText(/结果暂未核实/);
  api.lookupBulkCreates.mockRejectedValue(new Error("核对失败"));
  fireEvent.click(screen.getByRole("button", {name: "核对并重试待处理行"}));
  await waitFor(() => expect(api.lookupBulkCreates).toHaveBeenCalledTimes(1));
  expect(api.bulkCreate).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", {name: "编辑此行"})).toBeDisabled();
});
it("提交后行级INTERNAL_ERROR冻结原行并lookup找回已有单，不重复创建", async () => {
  await showRows(false);
  api.bulkCreate.mockImplementationOnce((_group: string, items: BulkCreateItem[]) => Promise.resolve({results: [result(items[0], "failed", "INTERNAL_ERROR")]}));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await screen.findByText("待核对：处理结果尚未确认，请先核对再重试");
  expect(screen.getByRole("button", {name: "编辑此行"})).toBeDisabled();
  expect(screen.getByRole("button", {name: /移\s*除/})).toBeDisabled();
  expect(screen.getByRole("textbox", {name: "S1 裙子约定日期"})).toBeDisabled();
  expect(screen.getByText(/成功 0，失败 0，待核对 1/)).toBeInTheDocument();
  const first = api.bulkCreate.mock.calls[0][1] as BulkCreateItem[];
  api.lookupBulkCreates.mockResolvedValue({results: [{...result(first[0]), reused: true}]});
  fireEvent.click(screen.getByRole("button", {name: "核对并重试待处理行"}));
  expect(await screen.findByText("成功（已存在，未重复创建）：草稿已保存")).toBeInTheDocument();
  expect(screen.getByText("单据 ID：id-s1")).toBeInTheDocument();
  expect(api.lookupBulkCreates).toHaveBeenCalledWith([first[0].client_create_key]);
  expect(api.bulkCreate).toHaveBeenCalledTimes(1);
});
it("套装成员不拆单，同一套装选中两款只创建一单并说明重复行", async () => {
  api.listGoodsForStyle.mockResolvedValue([{goods_main_id: "suit", goods_title: "上衣裙子套装", is_suit: true}]);
  await showRows();
  api.bulkCreate.mockImplementation((_group: string, items: BulkCreateItem[]) => Promise.resolve({results: items.map(item => result(item))}));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await waitFor(() => expect(api.bulkCreate).toHaveBeenCalledTimes(1));
  expect(api.bulkCreate.mock.calls[0][1]).toHaveLength(1);
  expect(screen.getByText(/相同商品\/套装已在本组/)).toBeInTheDocument();
});
it("共享默认日期生成后各行可独立覆盖", async () => {
  await showRows(true, "2026-12-20");
  const date = screen.getByRole("textbox", {name: "S1 裙子约定日期"});
  expect(date).toHaveValue("2026-12-20");
  fireEvent.change(date, {target: {value: "2026-12-22"}}); fireEvent.keyDown(date, {key: "Enter", code: "Enter"}); fireEvent.blur(date);
  api.bulkCreate.mockImplementation((_group: string, items: BulkCreateItem[]) => Promise.resolve({results: items.map(item => result(item))}));
  fireEvent.click(screen.getByRole("button", {name: "保存未成功的草稿"}));
  await waitFor(() => expect(api.bulkCreate).toHaveBeenCalledTimes(1));
  expect((api.bulkCreate.mock.calls[0][1] as BulkCreateItem[]).map(item => item.payload.scheduled_publish_date)).toEqual(["2026-12-22", "2026-12-20"]);
});
