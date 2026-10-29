"""推广商品归属：显式值优先，历史空值按非套装 / 货号推定一项。"""

PROMO_GOODS_LATERAL = """
LEFT JOIN LATERAL (
  SELECT gi.goods_main_id
  FROM goods_style_item gi
  JOIN goods_main gg ON gg.id = gi.goods_main_id
  WHERE gi.style_id = p.style_id AND gi.is_active = true
  ORDER BY gg.is_suit, gg.goods_code
  LIMIT 1
) owner ON p.goods_main_id IS NULL"""

PROMO_GOODS_ID = "COALESCE(p.goods_main_id, owner.goods_main_id)"
