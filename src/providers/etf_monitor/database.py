"""DuckDB 历史数据库 —— 长期数据积累"""
import os

import duckdb

from src.paths import DUCKDB_PATH

BASE_DIR = os.path.dirname(str(DUCKDB_PATH))
DB_PATH = str(DUCKDB_PATH)


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return duckdb.connect(DB_PATH)


def init_db(conn):
    """建表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nasdaq_daily (
            date DATE PRIMARY KEY,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, peak DOUBLE, drawdown DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etf_daily (
            run_date DATE,
            code VARCHAR,
            name VARCHAR,
            price DOUBLE, premium DOUBLE, premium_basis VARCHAR,
            amount DOUBLE, volume DOUBLE, nav DOUBLE, nav_date VARCHAR,
            iopv DOUBLE, change_pct DOUBLE,
            PRIMARY KEY (run_date, code)
        )
    """)
    # drawdown_events：仅当旧版 schema（单主键 run_date，无 event_id 列）时迁移重建一次，
    # 之后保留历史事件，不再每次 DROP。
    rows = conn.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'drawdown_events' AND column_name = 'event_id'
    """).fetchall()
    if not rows:
        # 表不存在或为旧版 schema → 迁移重建
        conn.execute('DROP TABLE IF EXISTS drawdown_events')
        conn.execute("""
            CREATE TABLE drawdown_events (
                event_id INTEGER,
                run_date DATE,
                threshold DOUBLE,
                start_date VARCHAR, bottom_date VARCHAR, recovery_date VARCHAR,
                max_drawdown DOUBLE, duration_days INTEGER, recovery_days INTEGER,
                PRIMARY KEY (run_date, event_id)
            )
        """)


def save_nasdaq(conn, df):
    """保存纳斯达克日线（含回撤列），按日期增量 upsert，避免全量 DELETE 导致 duckdb 文件膨胀"""
    conn.register('ndx_df', df)
    conn.execute("""
        INSERT OR REPLACE INTO nasdaq_daily
        SELECT date::DATE, open, high, low, close, volume, peak, drawdown
        FROM ndx_df
    """)
    conn.unregister('ndx_df')


def save_etfs(conn, etf_data_list, run_date):
    """保存 ETF 快照"""
    conn.execute('DELETE FROM etf_daily WHERE run_date = ?', [run_date])
    for d in etf_data_list:
        conn.execute("""
            INSERT INTO etf_daily
            (run_date, code, name, price, premium, premium_basis,
             amount, volume, nav, nav_date, iopv, change_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            run_date, d['code'], d['name'], d['price'], d['premium'],
            d['premium_basis'], d['amount'], d['volume'], d['nav'],
            d['nav_date'], d['iopv'], d['change_pct'],
        ])


def save_drawdown_events(conn, events, run_date):
    """保存回撤事件"""
    conn.execute('DELETE FROM drawdown_events WHERE run_date = ?', [run_date])
    for i, e in enumerate(events):
        conn.execute("""
            INSERT INTO drawdown_events
            (event_id, run_date, threshold, start_date, bottom_date,
             recovery_date, max_drawdown, duration_days, recovery_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            i, run_date, e['threshold'], e['start_date'], e['bottom_date'],
            e['recovery_date'], e['max_drawdown'], e['duration_days'],
            e['recovery_days'],
        ])
