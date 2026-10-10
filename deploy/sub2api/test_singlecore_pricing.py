"""Exact synthetic prices; all PG writes are in one random e2e schema."""
from decimal import Decimal
import json
import os
from pathlib import Path
import unittest
import uuid

import singlecore_pricing as p
import singlecore_supply as s

EXPR = '(len > 272000 ? tier("long", p * 8 + cr * 0.8 + c * 30 + cc * 10) : tier("standard", p * 4 + cr * 0.4 + c * 20 + cc * 5)) * ((param("service_tier") == "fast" || param("service_tier") == "priority") ? 2 : 1)'


def mapping():
    return p.build_mapping({"GroupRatio":'{"default":0.125}',"billing_setting.billing_expr":json.dumps({"synthetic-model":EXPR})},[])


class MappingTests(unittest.TestCase):
    def test_exact_threshold_cache_and_fast_rule(self):
        m=mapping();r=m["rules"][0]
        self.assertEqual("0.000004",r["card"]["input_price"])
        self.assertEqual("0.000008",r["card"]["intervals"][1]["input_price"])
        self.assertEqual("1",r["card"]["flex_multiplier"])
        self.assertEqual(68000,p.expected_quota(r,"0.125",dict(p=272000,cr=0,c=0,cc=0)))
        self.assertEqual(136001,p.expected_quota(r,"0.125",dict(p=272001,cr=0,c=0,cc=0)))
        self.assertEqual(272001,p.expected_quota(r,"0.125",dict(p=272001,cr=0,c=0,cc=0),"priority"))
        self.assertFalse(m["production_ready"])

    def test_unknown_expression_and_group_cannot_silently_fallback(self):
        m=p.build_mapping({"GroupRatio":'{"default":0.125}',"billing_setting.billing_expr":json.dumps({"unknown":'p * 2 + header("x-secret")'})},[])
        self.assertEqual("unsupported_expression",m["unmapped"][0]["reason"])
        self.assertNotIn("x-secret",json.dumps(m))
        with self.assertRaisesRegex(s.SupplyError,"GROUP_PRICE_MAPPING"):
            p.build_mapping({"GroupRatio":'{"default":0.125,"premium":1}'},[])

    def test_no_cc_term_preserves_unsplit_legacy_prompt_price(self):
        r=p.parse_expression("synthetic",EXPR.replace(" + cc * 10","").replace(" + cc * 5",""))
        self.assertEqual("4",r["usd_per_million"]["standard"]["cc"])


@unittest.skipUnless(os.getenv("REALYU_SUPPLY_TEST_CONFIG"),"explicit isolated PG required")
class PricingPGTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        cfg=s.load_config(os.environ["REALYU_SUPPLY_TEST_CONFIG"])
        self.assertEqual("realyu_funding_e2e",cfg["database"])
        self.conn=s.connect_config(cfg);self.conn.autocommit=True
        self.schema="pricing_"+uuid.uuid4().hex
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.conn.execute("CREATE TABLE groups(id bigint primary key,platform text,model_pricing jsonb,rate_multiplier numeric(20,12),long_context_pricing_enabled boolean)")
        self.conn.execute("CREATE TABLE channels(id bigserial primary key,name text unique,status text,billing_model_source text,restrict_models boolean)")
        self.conn.execute("CREATE TABLE channel_groups(channel_id bigint references channels(id),group_id bigint references groups(id),unique(group_id))")
        self.conn.execute("CREATE TABLE channel_model_pricing(id bigserial primary key,channel_id bigint references channels(id),platform text,models jsonb,billing_mode text,input_price numeric(20,12),output_price numeric(20,12),cache_read_price numeric(20,12),cache_write_price numeric(20,12),cache_write_1h_price numeric(20,12),fast_multiplier numeric(20,12),flex_multiplier numeric(20,12),image_input_price numeric(20,12),image_output_price numeric(20,8))")
        self.conn.execute("CREATE TABLE channel_pricing_intervals(id bigserial primary key,pricing_id bigint references channel_model_pricing(id),min_tokens int,max_tokens int,tier_label text,sort_order int,input_price numeric(20,12),output_price numeric(20,12),cache_read_price numeric(20,12),cache_write_price numeric(20,12),cache_write_1h_price numeric(20,12))")
        self.conn.execute("CREATE TABLE users(id bigint,role text);CREATE TABLE accounts(id bigint,schedulable boolean);CREATE TABLE api_keys(id bigint);CREATE TABLE usage_logs(id bigint);CREATE TABLE payment_orders(id bigint)")
        self.conn.execute("INSERT INTO groups VALUES(2,'openai','[]',1,false)")

    def tearDown(self):
        from psycopg import sql
        self.conn.execute("SET search_path TO public")
        self.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.conn.close()

    def test_atomic_price_cards_preserve_exact_numeric_and_no_repeat_overwrite(self):
        result=p.apply_rehearsal(self.conn,mapping(),2)
        self.assertEqual(1,result["model_count"])
        self.assertEqual((Decimal("0.125"),True),self.conn.execute("SELECT rate_multiplier,long_context_pricing_enabled FROM groups WHERE id=2").fetchone())
        self.assertEqual([(0,272000,Decimal("0.000004")),(272000,None,Decimal("0.000008"))],self.conn.execute("SELECT min_tokens,max_tokens,input_price FROM channel_pricing_intervals ORDER BY sort_order").fetchall())
        with self.assertRaisesRegex(s.SupplyError,"GROUP_ALREADY_HAS_CHANNEL"):p.apply_rehearsal(self.conn,mapping(),2)

    def test_customer_or_active_scheduler_rejected_without_changes(self):
        self.conn.execute("INSERT INTO accounts VALUES(1,true)")
        with self.assertRaisesRegex(s.SupplyError,"SCHEDULING_MUST_BE_DISABLED"):p.apply_rehearsal(self.conn,mapping(),2)
        self.conn.execute("DELETE FROM accounts;INSERT INTO users VALUES(1,'user')")
        with self.assertRaisesRegex(s.SupplyError,"HAS_CUSTOMERS"):p.apply_rehearsal(self.conn,mapping(),2)
        self.assertEqual(0,self.conn.execute("SELECT count(*) FROM channels").fetchone()[0])

    def test_precision_loss_rolls_back_entire_mapping(self):
        m=mapping();m["rules"][0]["card"]["input_price"]="0.0000000000001"
        m.pop("mapping_sha256");m["mapping_sha256"]=s.digest(m)
        with self.assertRaisesRegex(s.SupplyError,"DATABASE_PRICE_PRECISION_LOSS"):p.apply_rehearsal(self.conn,m,2)
        self.assertEqual(0,self.conn.execute("SELECT count(*) FROM channels").fetchone()[0])
        self.assertEqual(Decimal(1),self.conn.execute("SELECT rate_multiplier FROM groups").fetchone()[0])

    def test_native_image_ratio_has_explicit_flat_image_token_prices(self):
        m=p.build_mapping({"GroupRatio":'{"default":0.125}',"ModelRatio":'{"gpt-image-2":1}',
                           "CompletionRatio":'{"gpt-image-2":1}'},[])
        p.apply_rehearsal(self.conn,m,2)
        self.assertEqual((Decimal("0.000002"),Decimal("0.000002"),Decimal("0.000002"),Decimal("0.000002")),
                         self.conn.execute("SELECT input_price,output_price,image_input_price,image_output_price FROM channel_model_pricing").fetchone())
        self.assertEqual([(Decimal("0.000002"),),(Decimal("0.000002"),)],self.conn.execute("SELECT output_price FROM channel_pricing_intervals ORDER BY sort_order").fetchall())
        self.assertEqual("1",m["rules"][0]["card"]["fast_multiplier"])
        self.assertEqual(1,p.expected_quota(m["rules"][0],"0.125",dict(p=1,cr=0,c=0,cc=0)))


if __name__=="__main__":unittest.main()
