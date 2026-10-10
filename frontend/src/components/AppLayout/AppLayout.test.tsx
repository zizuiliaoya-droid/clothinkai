// @vitest-environment jsdom
// 左侧菜单「仓库发货」：只对仓库 / 管理员显示（PR-2 起仓库页只走 promotion_ship:fill / export，其余角色点进去是 403）。
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/features/auth/api", () => ({ logout: vi.fn() }));

import { AppLayout } from "@/components/AppLayout/AppLayout";
import { useAuthStore } from "@/stores/authStore";

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

function renderAs(roles: string[]) {
  useAuthStore.setState({
    user: { id: "u1", username: "u1", display_name: null, email: null, status: "active", roles },
    isAuthenticated: true,
  });
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AppLayout />
    </MemoryRouter>
  );
}

describe("AppLayout 菜单「仓库发货」", () => {
  it.each([["pr"], ["pr_manager"], ["finance"], ["operations"]])(
    "%s 没有发货权限：不显示「仓库发货」",
    (role) => {
      renderAs([role]);
      // 场景有效性：推广管理分组是展开的，其他菜单项在
      expect(screen.getByText("站外推广")).toBeTruthy();
      expect(screen.getByText("催发任务")).toBeTruthy();
      expect(screen.queryByText("仓库发货")).toBeNull();
    }
  );

  it.each([["admin"], ["platform_admin"]])("%s 显示「仓库发货」", (role) => {
    renderAs([role]);
    expect(screen.getByText("站外推广")).toBeTruthy();
    expect(screen.getByText("仓库发货")).toBeTruthy();
  });

  it("仓库角色照旧只有「仓库发货」一项", () => {
    renderAs(["warehouse"]);
    expect(screen.getByText("仓库发货")).toBeTruthy();
    expect(screen.queryByText("站外推广")).toBeNull();
  });
});
