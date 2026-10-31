export const ROLE_OPTIONS = [
  { value: "admin", label: "管理员" },
  { value: "designer", label: "设计师" },
  { value: "design_assistant", label: "设计助理" },
  { value: "pattern_maker", label: "版师" },
  { value: "merchandiser", label: "跟单" },
  { value: "pr", label: "PR" },
  { value: "pr_manager", label: "PR 主管" },
  { value: "finance", label: "财务" },
  { value: "operations", label: "运营" },
  { value: "warehouse", label: "仓库" },
];

export function roleLabel(code: string) {
  if (code === "platform_admin") return "平台管理员";
  return ROLE_OPTIONS.find((role) => role.value === code)?.label ?? code;
}
