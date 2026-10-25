-- Disposable CI only, after the original 001..066 migrations. No production rows.
BEGIN;
INSERT INTO tenant(id,code,name) VALUES
 ('10000000-0000-0000-0000-000000000001','acceptance-ci-a','Synthetic A'),
 ('10000000-0000-0000-0000-000000000002','acceptance-ci-b','Synthetic B');
INSERT INTO style(id,tenant_id,style_code,style_name)
 SELECT ('20000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,
 ('10000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,'CI-'||i,'Synthetic'
 FROM generate_series(1,2) i;
INSERT INTO blogger(id,tenant_id,xiaohongshu_id,nickname)
 SELECT ('30000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,
 ('10000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,'ci-'||i,'Synthetic'
 FROM generate_series(1,2) i;
INSERT INTO promotion(id,tenant_id,style_id,blogger_id,internal_code,
 style_code_snapshot,style_short_name_snapshot,quote_amount,cost_snapshot,
 return_shipping_fee,return_shipping_payer,platform,cooperation_date,
 publish_status,settlement_status,recall_status,retro_status,metrics_recorded_at)
 SELECT ('40000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,
 ('10000000-0000-0000-0000-'||lpad((1+i%2)::text,12,'0'))::uuid,
 ('20000000-0000-0000-0000-'||lpad((1+i%2)::text,12,'0'))::uuid,
 ('30000000-0000-0000-0000-'||lpad((1+i%2)::text,12,'0'))::uuid,
 'ACCEPTANCE-CI-'||i,'CI-style','Synthetic',100,20,30,'博主垫付','小红书',DATE '2026-01-01',
 CASE i%5 WHEN 1 THEN '已发布' WHEN 2 THEN '已发布' WHEN 3 THEN '已发布' WHEN 4 THEN '已取消' ELSE '未发布' END,
 CASE i%5 WHEN 1 THEN '已结款' ELSE '未核查' END,
 CASE i%5 WHEN 4 THEN '召回成功' ELSE '未召回' END,
 CASE i%5 WHEN 1 THEN '已完成' WHEN 2 THEN '待确认' ELSE '未开始' END,
 CASE i%5 WHEN 1 THEN TIMESTAMPTZ '2026-01-10T00:00:00Z' ELSE NULL END
 FROM generate_series(1,5000) i;
INSERT INTO promotion_platform_setting(id,tenant_id,platform,like_coefficient,hit_threshold)
 SELECT ('50000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,
 ('10000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,'小红书',1,100
 FROM generate_series(1,2) i;
INSERT INTO blogger_retrospective(id,tenant_id,blogger_id,promotion_id,content)
 VALUES('60000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001',
 '30000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','Synthetic legacy unconfirmed');
INSERT INTO attachment(id,tenant_id,bucket,r2_key,purpose,mime_type,size_bytes,status)
 SELECT ('70000000-0000-0000-0000-'||lpad(i::text,12,'0'))::uuid,
 '10000000-0000-0000-0000-000000000001','private','ci-no-object-'||i,
 'promotion_payment_qr','image/png',1,'ready' FROM generate_series(1,2) i;
INSERT INTO return_freight_payment(id,tenant_id,promotion_id,amount,status,
 payment_qr_attachment_id,waybill_attachment_id,submitted_at)
 VALUES('80000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001',
 '40000000-0000-0000-0000-000000000004',30,'待审核','70000000-0000-0000-0000-000000000001',
 '70000000-0000-0000-0000-000000000002',TIMESTAMPTZ '2026-01-11T00:00:00Z');
INSERT INTO settlement(id,tenant_id,promotion_id,blogger_id,style_id,settlement_no,
 amount,total_amount,settlement_status,request_event_id)
 VALUES('90000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000002',
 '40000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000002',
 '20000000-0000-0000-0000-000000000002','SYNTHETIC-CI-1',100,130,'待付款',
 'a0000000-0000-0000-0000-000000000001');
INSERT INTO settlement_extra_item(id,tenant_id,settlement_id,item_type,amount)
 VALUES('b0000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000002',
 '90000000-0000-0000-0000-000000000001','运费',30);
INSERT INTO line_event(id,tenant_id,promotion_id,line_id,event_type,payload)
 SELECT 'c0000000-0000-0000-0000-000000000001',tenant_id,id,line_id,'recall.succeeded',
 '{"fixture":true,"amount":"30.00"}'::jsonb FROM promotion
 WHERE id='40000000-0000-0000-0000-000000000004';
COMMIT;
