import threading
import time
import ccxt
import pandas as pd
import numpy as np

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.switch import Switch
from kivy.uix.spinner import Spinner

class AutoTraderApp(App):
    def build(self):
        self.title = "MEXC Auto-Trader Mobile v3.1"
        self.is_running = False
        self.engine_thread = None
        self.active_position = None
        self.demo_balance = 100.0
        self.trade_amount_usdt = 13.0

        self.exchange_public = ccxt.mexc({'enableRateLimit': True, 'timeout': 10000})
        self.exchange_private = None

        main_layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        # Top Controls
        mode_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=40, spacing=5)
        mode_box.add_widget(Label(text="Mode:", size_hint_x=0.3))
        self.mode_spinner = Spinner(text="Real", values=("Demo", "Real"), size_hint_x=0.7)
        mode_box.add_widget(self.mode_spinner)
        main_layout.add_widget(mode_box)

        # API Inputs
        self.api_key_entry = TextInput(hint_text="MEXC API Key", password=True, multiline=False, size_hint_y=None, height=40)
        self.api_secret_entry = TextInput(hint_text="MEXC Secret Key", password=True, multiline=False, size_hint_y=None, height=40)
        main_layout.add_widget(self.api_key_entry)
        main_layout.add_widget(self.api_secret_entry)

        # Trade Inputs
        trade_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=40, spacing=5)
        self.trade_size_entry = TextInput(text="13", multiline=False)
        self.threshold_entry = TextInput(text="70", multiline=False)
        trade_box.add_widget(Label(text="Size (USDT):"))
        trade_box.add_widget(self.trade_size_entry)
        trade_box.add_widget(Label(text="Score %:"))
        trade_box.add_widget(self.threshold_entry)
        main_layout.add_widget(trade_box)

        # Action Buttons
        btn_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=50, spacing=10)
        self.start_btn = Button(text="Start Bot", background_color=(0.2, 0.8, 0.2, 1))
        self.start_btn.bind(on_press=self.start_bot)
        self.stop_btn = Button(text="Stop Bot", background_color=(0.8, 0.2, 0.2, 1), disabled=True)
        self.stop_btn.bind(on_press=self.stop_bot)
        btn_box.add_widget(self.start_btn)
        btn_box.add_widget(self.stop_btn)
        main_layout.add_widget(btn_box)

        # Monitor Panel
        self.status_lbl = Label(text="Status: IDLE", size_hint_y=None, height=30, color=(1, 0.8, 0, 1))
        self.pnl_lbl = Label(text="PnL: $0.00 (0.00%)", size_hint_y=None, height=30)
        self.close_btn = Button(text="Close Position Now", size_hint_y=None, height=40, disabled=True, background_color=(0.9, 0.1, 0.1, 1))
        self.close_btn.bind(on_press=self.manual_close_position)

        main_layout.add_widget(self.status_lbl)
        main_layout.add_widget(self.pnl_lbl)
        main_layout.add_widget(self.close_btn)

        # Console Logs Area
        main_layout.add_widget(Label(text="Console Logs:", size_hint_y=None, height=25))
        self.log_label = Label(text="", size_hint_y=None, markup=True)
        self.log_label.bind(texture_size=lambda instance, value: setattr(instance, 'height', value[1]))
        
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(self.log_label)
        main_layout.add_widget(scroll)

        return main_layout

    def log(self, msg):
        ts = time.strftime("[%H:%M:%S] ")
        def update_log(dt):
            self.log_label.text += f"\n{ts}{msg}"
        Clock.schedule_once(update_log)

    def get_real_ticker_price(self, symbol):
        try:
            ticker = self.exchange_public.fetch_ticker(symbol)
            return float(ticker['last'])
        except Exception:
            return None

    def start_bot(self, instance):
        if self.mode_spinner.text == "Real":
            key = self.api_key_entry.text.strip()
            secret = self.api_secret_entry.text.strip()
            if not key or not secret:
                self.log("Error: Real Mode requires API Key and Secret!")
                return
            self.exchange_private = ccxt.mexc({'apiKey': key, 'secret': secret, 'enableRateLimit': True})

        try:
            self.trade_amount_usdt = float(self.trade_size_entry.text)
        except ValueError:
            self.log("Error: Invalid Trade Size Value.")
            return

        self.is_running = True
        self.start_btn.disabled = True
        self.stop_btn.disabled = False
        self.mode_spinner.disabled = True

        self.log(f"Auto-Trader STARTED in [{self.mode_spinner.text.upper()}] Mode.")
        self.engine_thread = threading.Thread(target=self.core_trading_loop, daemon=True)
        self.engine_thread.start()

    def stop_bot(self, instance):
        self.is_running = False
        self.start_btn.disabled = False
        self.stop_btn.disabled = True
        self.mode_spinner.disabled = False
        self.log("Auto-Trader STOPPED by user.")

    def core_trading_loop(self):
        while self.is_running:
            if self.active_position is None:
                Clock.schedule_once(lambda dt: setattr(self.status_lbl, 'text', "Status: SCANNING MARKET..."))
                self.scan_and_trigger_first_signal()
            else:
                self.monitor_active_position()
            time.sleep(1)

    def scan_and_trigger_first_signal(self):
        try:
            min_score = float(self.threshold_entry.text)
        except ValueError:
            min_score = 70.0

        try:
            markets = self.exchange_public.load_markets()
            pairs = [s for s in markets if s.endswith('/USDT') and not any(x in s for x in ['3S', '3L', 'BEAR', 'BULL', 'USDC'])][:150]

            for sym in pairs:
                if not self.is_running or self.active_position is not None:
                    break
                sig = self.analyze_symbol(sym)
                if sig and sig['score_num'] >= min_score:
                    self.execute_entry_order(sig)
                    break
                time.sleep(0.05)
        except Exception as e:
            self.log(f"Scan Error: {e}")

    def analyze_symbol(self, symbol):
        try:
            ohlcv = self.exchange_public.fetch_ohlcv(symbol, timeframe='15m', limit=60)
            if not ohlcv or len(ohlcv) < 50: return None

            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['sma20'] = df['close'].rolling(20).mean()
            df['std'] = df['close'].rolling(20).std()
            df['upper'] = df['sma20'] + (df['std'] * 2)
            df['lower'] = df['sma20'] - (df['std'] * 2)
            df['width'] = (df['upper'] - df['lower']) / df['sma20']

            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            df['rsi'] = 100 - (100 / (1 + (gain / (loss + 1e-9))))

            tr = np.maximum(df['high'] - df['low'], np.abs(df['high'] - df['close'].shift()), np.abs(df['low'] - df['close'].shift()))
            df['atr'] = pd.Series(tr).rolling(14).mean()

            last, prev = df.iloc[-1], df.iloc[-2]
            score = 0
            if last['close'] > last['ema50']: score += 20
            if last['vol'] > (df['vol'].iloc[-21:-1].mean() * 2.0): score += 30
            if prev['width'] < 0.035 and last['close'] > last['upper']: score += 30
            if 48 <= last['rsi'] <= 70: score += 20

            entry = self.get_real_ticker_price(symbol)
            if not entry or entry <= 0:
                entry = float(last['close'])

            raw_atr = last['atr']
            atr = float(raw_atr) if not pd.isna(raw_atr) and float(raw_atr) > 0 else entry * 0.015

            sl = entry - (abs(atr) * 1.5)
            tp = entry + (abs(atr) * 3.0)

            return {'symbol': symbol, 'score_num': score, 'entry': entry, 'sl': sl, 'tp': tp}
        except Exception:
            return None

    def execute_entry_order(self, sig):
        mode = self.mode_spinner.text
        sym = sig['symbol']
        
        real_market_price = self.get_real_ticker_price(sym)
        entry_price = real_market_price if real_market_price else sig['entry']
        amount_coins = self.trade_amount_usdt / entry_price

        if mode == "Real":
            try:
                order = self.exchange_private.create_market_buy_order(sym, amount_coins)
                time.sleep(0.5)
                filled_price = self.get_real_ticker_price(sym)
                if filled_price:
                    entry_price = filled_price
            except Exception as e:
                self.log(f"API Real Order Failed: {e}")
                return

        atr_estimate = entry_price * 0.015
        sl_final = entry_price - (atr_estimate * 1.5)
        tp_final = entry_price + (atr_estimate * 3.0)

        self.active_position = {
            'symbol': sym,
            'mode': mode,
            'entry': entry_price,
            'sl': sl_final,
            'tp': tp_final,
            'amount_usdt': self.trade_amount_usdt,
            'amount_coins': self.trade_amount_usdt / entry_price,
            'entry_time': time.strftime("%H:%M:%S")
        }

        Clock.schedule_once(lambda dt: setattr(self.close_btn, 'disabled', False))
        Clock.schedule_once(lambda dt: setattr(self.status_lbl, 'text', f"ACTIVE POSITION: [{sym}] @ {entry_price:.6f}"))
        self.log(f"ORDER EXECUTED ({mode}): Bought {sym} @ {entry_price:.6f}")

    def monitor_active_position(self):
        pos = self.active_position
        if not pos: return

        curr_price = self.get_real_ticker_price(pos['symbol'])
        if not curr_price:
            time.sleep(2)
            return

        entry = pos['entry']
        pnl_pct = ((curr_price - entry) / entry) * 100
        pnl_usdt = (curr_price - entry) * pos['amount_coins']

        Clock.schedule_once(lambda dt: setattr(self.pnl_lbl, 'text', f"PnL: ${pnl_usdt:+.2f} ({pnl_pct:+.2f}%)"))

        if curr_price >= pos['tp']:
            self.close_position(curr_price, "Take-Profit Hit 🎯")
        elif curr_price <= pos['sl']:
            self.close_position(curr_price, "Stop-Loss Hit 🛑")

        time.sleep(1.5)

    def manual_close_position(self, instance):
        if self.active_position:
            curr_price = self.get_real_ticker_price(self.active_position['symbol'])
            if not curr_price:
                curr_price = self.active_position['entry']
            self.close_position(curr_price, "Manual Close 👤")

    def close_position(self, exit_price, reason):
        pos = self.active_position
        if not pos: return

        if pos['mode'] == "Real":
            try:
                self.exchange_private.create_market_sell_order(pos['symbol'], pos['amount_coins'])
            except Exception as e:
                self.log(f"API Sell Order Error: {e}")

        pnl_usdt = (exit_price - pos['entry']) * pos['amount_coins']
        self.log(f"CLOSED [{pos['symbol']}] via {reason} @ {exit_price:.6f} | PnL: ${pnl_usdt:+.2f}")

        self.active_position = None
        Clock.schedule_once(lambda dt: setattr(self.close_btn, 'disabled', True))
        Clock.schedule_once(lambda dt: setattr(self.pnl_lbl, 'text', "PnL: $0.00 (0.00%)"))
        Clock.schedule_once(lambda dt: setattr(self.status_lbl, 'text', "Status: SEARCHING FOR OPPORTUNITY..."))

if __name__ == "__main__":
    AutoTraderApp().run()