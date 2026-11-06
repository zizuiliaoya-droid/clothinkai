// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { BloggerClaimsModal } from "./BloggerClaimsModal";
import type { Blogger } from "./types";

const api = vi.hoisted(() => ({listWorkWechats: vi.fn(), listClaims: vi.fn(), listClaimPrOptions: vi.fn(), selfClaim: vi.fn(), assignClaims: vi.fn(), revokeClaim: vi.fn(), switchClaimWechat: vi.fn(), transferClaim: vi.fn()}));
const mode = vi.hoisted(() => ({readonly: false}));
vi.mock("./claims", () => api);
vi.mock("@/stores/supportStore", () => ({useReadOnlySupport: () => mode.readonly}));
const blogger = {id: "b1", nickname: "博主甲", can_self_claim: true, can_manage_claims: false} as Blogger;
const clients: QueryClient[] = [];
function show() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  clients.push(client);
  render(<QueryClientProvider client={client}><BloggerClaimsModal targets={[blogger]} onClose={() => {}} /></QueryClientProvider>);
}
beforeAll(() => {
  const style = window.getComputedStyle.bind(window);
  window.getComputedStyle = element => style(element);
  Object.defineProperty(window, "matchMedia", {writable: true, value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});
});
beforeEach(() => {
  mode.readonly = false;
  for (const fn of Object.values(api)) fn.mockReset();
  api.listWorkWechats.mockResolvedValue({items: [{id: "w1", value: "工作一号", is_active: true}], can_manage: false});
  api.listClaims.mockResolvedValue([]);
  api.selfClaim.mockResolvedValue([{blogger_id: "b1", status: "claimed"}]);
});
afterEach(() => {cleanup(); clients.splice(0).forEach(c => c.clear());});

it("本人输入微信并选主账号完成认领，接口不给原微信也可操作", async () => {
  show();
  fireEvent.change(screen.getByLabelText("博主甲的登记微信"), {target: {value: " wx-original "}});
  fireEvent.mouseDown(screen.getByRole("combobox", {name: "认领工作微信"}));
  fireEvent.click(await screen.findByText("工作一号"));
  fireEvent.click(screen.getByRole("button", {name: "验证并认领"}));
  await waitFor(() => expect(api.selfClaim).toHaveBeenCalledWith([{blogger_id: "b1", wechat: "wx-original", work_wechat_id: "w1"}]));
  expect(await screen.findByText("博主甲：认领成功")).toBeTruthy();
});

it("支持会话不出现认领或分配写入口", async () => {
  mode.readonly = true;
  show();
  await waitFor(() => expect(api.listClaims).toHaveBeenCalled());
  expect(screen.queryByRole("button", {name: "验证并认领"})).toBeNull();
  expect(screen.queryByRole("button", {name: "追加分配"})).toBeNull();
  expect(api.selfClaim).not.toHaveBeenCalled();
});
