import type { Blogger, BloggerCreate, BloggerUpdate } from "./types";

/**
 * 编辑博主的提交内容：报价备注只在用户真的改了它时才放进提交。
 *
 * 读不到报价的人（含「保留报价写权限、去掉读权限」的个人授权，前端按角色判断不出来）
 * 收到的报价备注是遮挡后的 null；原样提交会把库里的值清空。没改就不带，后端也会拒绝
 * 读不到报价的人显式带报价备注（403）。
 * 没渲染这个表单项时（无写权限）值是 undefined，同样不带。
 */
export function toBloggerUpdate(
  values: BloggerCreate,
  record: Pick<Blogger, "quote_note">
): BloggerUpdate {
  const { quote_note: quoteNote, ...rest } = values;
  if (quoteNote === undefined) return rest;
  // 后端会去首尾空白、空串当 null，比较时同样处理
  const same = (quoteNote ?? "").trim() === (record.quote_note ?? "").trim();
  return same ? rest : { ...rest, quote_note: quoteNote };
}
