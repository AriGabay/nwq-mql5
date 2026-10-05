//+------------------------------------------------------------------+
//| duka_tick_dump.mq5 - Dukascopy data check (2026-10-05)            |
//| Diagnostic EA for the isolated Strategy Tester only: writes every |
//| tick the Tester delivers (time_msc, bid, ask, flags) and the      |
//| symbol specification. It never sends an order.                    |
//+------------------------------------------------------------------+
#property strict

input string ResearchRunTag = "";
input long   ReadbackFromMsc = 0;   // > 0: also write the stored ticks of [from, to] (CopyTicksRange) at init
input long   ReadbackToMsc   = 0;

int hTicks = INVALID_HANDLE;

string Out(string name) { return "rl_" + name + "_" + ResearchRunTag + ".csv"; }

void Spec()
  {
   int h = FileOpen(Out("spec"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE) return;
   FileWrite(h, "key", "value");
   FileWrite(h, "symbol", _Symbol);
   FileWrite(h, "digits", (long)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   FileWrite(h, "point", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_POINT), 8));
   FileWrite(h, "tick_size", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), 8));
   FileWrite(h, "tick_value", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), 8));
   FileWrite(h, "contract_size", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE), 2));
   FileWrite(h, "currency_base", SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE));
   FileWrite(h, "currency_profit", SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT));
   FileWrite(h, "currency_margin", SymbolInfoString(_Symbol, SYMBOL_CURRENCY_MARGIN));
   FileWrite(h, "calc_mode", (long)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_CALC_MODE));
   FileWrite(h, "stops_level", (long)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL));
   FileWrite(h, "freeze_level", (long)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL));
   FileWrite(h, "spread_float", (long)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD_FLOAT));
   FileWrite(h, "custom", (long)SymbolInfoInteger(_Symbol, SYMBOL_CUSTOM));
   FileWrite(h, "formula", SymbolInfoString(_Symbol, SYMBOL_FORMULA));
   FileWrite(h, "basis", SymbolInfoString(_Symbol, SYMBOL_BASIS));
   FileWrite(h, "path", SymbolInfoString(_Symbol, SYMBOL_PATH));
   FileWrite(h, "description", SymbolInfoString(_Symbol, SYMBOL_DESCRIPTION));
   FileWrite(h, "chart_mode", (long)SymbolInfoInteger(_Symbol, SYMBOL_CHART_MODE));
   FileWrite(h, "trade_mode", (long)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE));
   FileWrite(h, "volume_min", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), 4));
   FileWrite(h, "volume_step", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), 4));
   FileWrite(h, "volume_max", DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), 4));
   for(int d = 0; d < 7; d++)
     {
      datetime a, b;
      for(int s = 0; SymbolInfoSessionQuote(_Symbol, (ENUM_DAY_OF_WEEK)d, s, a, b); s++)
         FileWrite(h, "quote_session_" + IntegerToString(d) + "_" + IntegerToString(s),
                   TimeToString(a, TIME_MINUTES) + "-" + TimeToString(b, TIME_MINUTES));
      for(int s = 0; SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)d, s, a, b); s++)
         FileWrite(h, "trade_session_" + IntegerToString(d) + "_" + IntegerToString(s),
                   TimeToString(a, TIME_MINUTES) + "-" + TimeToString(b, TIME_MINUTES));
     }
   FileClose(h);
  }

// the ticks (and M1 bars) the Tester holds for the symbol in a range, as stored (independent of the ticks it
// delivers); written at init ("readback") and again at deinit ("readback_end"), when the test time is at its end
void Readback(string name)
  {
   MqlRates r[];
   int nr = CopyRates(_Symbol, PERIOD_M1, (datetime)(ReadbackFromMsc / 1000), (datetime)(ReadbackToMsc / 1000), r);
   int hr = FileOpen(Out(name + "_rates"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(hr != INVALID_HANDLE)
     {
      FileWrite(hr, "time", "open", "high", "low", "close", "tick_volume", "spread");
      for(int i = 0; i < nr; i++)
         FileWrite(hr, IntegerToString((long)r[i].time), DoubleToString(r[i].open, _Digits),
                   DoubleToString(r[i].high, _Digits), DoubleToString(r[i].low, _Digits),
                   DoubleToString(r[i].close, _Digits), IntegerToString(r[i].tick_volume), IntegerToString(r[i].spread));
      FileClose(hr);
     }
   MqlTick t[];
   int n = -1, err = 0;
   for(int k = 0; k < 10 && n <= 0; k++)
     {
      ResetLastError();
      n = CopyTicksRange(_Symbol, t, COPY_TICKS_ALL, (ulong)ReadbackFromMsc, (ulong)ReadbackToMsc);
      err = GetLastError();
      if(n <= 0) Sleep(500);
     }
   int h = FileOpen(Out(name), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE) return;
   FileWrite(h, "time_msc", "bid", "ask", "flags");
   for(int i = 0; i < n; i++)
      FileWrite(h, IntegerToString(t[i].time_msc), DoubleToString(t[i].bid, _Digits), DoubleToString(t[i].ask, _Digits),
                IntegerToString(t[i].flags));
   FileClose(h);
   int m = FileOpen(Out(name + "_meta"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(m == INVALID_HANDLE) return;
   FileWrite(m, "key", "value");
   FileWrite(m, "from_msc", IntegerToString(ReadbackFromMsc));
   FileWrite(m, "to_msc", IntegerToString(ReadbackToMsc));
   FileWrite(m, "copied", IntegerToString(n));
   FileWrite(m, "last_error", IntegerToString(err));
   FileClose(m);
  }

int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER)) { Print("duka_tick_dump: tester only"); return INIT_FAILED; }
   Spec();
   if(ReadbackToMsc > 0) Readback("readback");
   hTicks = FileOpen(Out("ticks"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(hTicks == INVALID_HANDLE) return INIT_FAILED;
   FileWrite(hTicks, "time_msc", "bid", "ask", "flags");
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t)) return;
   FileWrite(hTicks, IntegerToString(t.time_msc), DoubleToString(t.bid, _Digits), DoubleToString(t.ask, _Digits),
             IntegerToString(t.flags));
  }

void OnDeinit(const int reason)
  {
   if(hTicks != INVALID_HANDLE) FileClose(hTicks);
   if(ReadbackToMsc > 0) Readback("readback_end");
  }
