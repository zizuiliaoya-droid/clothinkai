import { apiClient } from "@/services/apiClient";

export interface AccountIdentity { account_id: string; platform: string; account: string; nickname: string }
export interface AccountOption { id: string; platform: string; account: string; nickname: string; available_for_new_cooperation: boolean }
export interface AccountSnapshot {
  version: 1;
  primary_account_id: string;
  group_id: string | null;
  accounts: AccountIdentity[];
  quote_id: string | null;
  reference_amount: string | null;
  price_source: "group_quote" | "blogger_quote" | "manual" | "unknown" | "exchange";
}
export interface AccountGroupData {
  group: {id: string; name: string; account_ids: string[]; quotes: {id: string; account_ids: string[]; amount: string | null}[]} | null;
  accounts: AccountOption[];
  can_manage: boolean;
  can_read_quote: boolean;
  can_write_quote: boolean;
  can_write_amount: boolean;
}
export interface AccountQuotePreview {
  account_snapshot: AccountSnapshot;
  reference_amount: string | null;
  matched: boolean;
  can_write_amount: boolean;
  can_read_quote: boolean;
}
export interface AccountFulfillment { total: number; completed: number; missing_accounts: AccountIdentity[]; complete: boolean }

export function accountLabel(account: Pick<AccountIdentity, "nickname" | "platform" | "account">) {
  return `${account.nickname} · ${account.platform} · ${account.account}`;
}
export async function getAccountGroup(bloggerId: string): Promise<AccountGroupData> {
  return (await apiClient.get<AccountGroupData>(`/api/bloggers/${bloggerId}/account-group`)).data;
}
export async function saveAccountGroup(id: string | null, payload: {name: string; account_ids: string[]}): Promise<AccountGroupData> {
  return id
    ? (await apiClient.put<AccountGroupData>(`/api/bloggers/account-groups/${id}`, payload)).data
    : (await apiClient.post<AccountGroupData>("/api/bloggers/account-groups", payload)).data;
}
export async function saveAccountQuote(groupId: string, payload: {account_ids: string[]; amount: string}): Promise<AccountGroupData> {
  return (await apiClient.put<AccountGroupData>(`/api/bloggers/account-groups/${groupId}/quotes`, payload)).data;
}
export async function resolveAccountQuote(bloggerId: string, accountIds: string[]): Promise<AccountQuotePreview> {
  return (await apiClient.post<AccountQuotePreview>("/api/bloggers/account-quotes/resolve", {blogger_id: bloggerId, account_ids: accountIds})).data;
}
