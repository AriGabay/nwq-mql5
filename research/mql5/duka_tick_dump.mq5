//+------------------------------------------------------------------+
//| duka_tick_dump.mq5 - Dukascopy data check (2026-10-05)            |
//| Diagnostic EA for the isolated Strategy Tester only: writes every |
//| tick the Tester delivers (time_msc, bid, ask, flags) and the      |
//| symbol specification. It never sends an order.                    |
//+------------------------------------------------------------------+
#property strict

input string ResearchRunTag = "";

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

int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER)) { Print("duka_tick_dump: tester only"); return INIT_FAILED; }
   Spec();
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
  }
