//+------------------------------------------------------------------+
//| session_probe.mq5 - Strategy Tester diagnostic (research only)    |
//| Not a trading EA. It answers, inside the isolated tester only:    |
//|  1. the symbol's quote and trade sessions as the tester sees them |
//|  2. Bid/Ask ticks in given windows (sessions opens, case exits)   |
//|  3. whether a Market order is accepted on the first tick after a  |
//|     quote gap, and on the first tick of the next minute           |
//|  4. replicas of given positions (side, SL, TP) opened in the last |
//|     bar before a gap: when and why the tester closes them         |
//+------------------------------------------------------------------+
#property copyright   "nwq-mql5 research"
#property version     "1.00"
#property description "Tester-only probe of sessions, ticks, order acceptance and SL/TP execution"
#property tester_file "probe_cases.csv"
#property tester_file "probe_windows.csv"

#include <Trade/Trade.mqh>

input string ResearchRunTag = "";

struct Case
  {
   long   id;
   int    dir;
   double sl, tp;
   long   openFrom;     // ms: first tick at/after this opens the replica ...
   long   openTo;       // ms: ... while before this (the gap)
   bool   tried, opened, closed;
   int    attempts;
   ulong  pos;
  };

Case   C[];
long   W0[], W1[];      // merged tick windows (ms), sorted
int    wk = 0;
CTrade trade;
int    hTicks = INVALID_HANDLE, hOrders = INVALID_HANDLE, hRep = INVALID_HANDLE;
long   lastMs = 0;
long   openMin = -1;    // minute index of the last session open
bool   probedNext = true;
ulong  probePos = 0;
long   nextOpenMs = LONG_MAX;

string Out(string name) { return "rl_" + name + "_" + ResearchRunTag + ".csv"; }

void WriteSessions()
  {
   int h = FileOpen(Out("sessions"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   FileWrite(h, "kind", "weekday", "index", "from_s", "to_s");
   for(int d = 0; d < 7; d++)
      for(int i = 0; i < 10; i++)
        {
         datetime f, t;
         if(SymbolInfoSessionQuote(_Symbol, (ENUM_DAY_OF_WEEK)d, i, f, t))
            FileWrite(h, "quote", d, i, (long)f, (long)t);
         if(SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)d, i, f, t))
            FileWrite(h, "trade", d, i, (long)f, (long)t);
        }
   FileWrite(h, "spec_trade_mode", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE), 0, 0, 0);
   FileWrite(h, "spec_stops_level", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL), 0, 0, 0);
   FileWrite(h, "spec_freeze_level", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL), 0, 0, 0);
   FileWrite(h, "spec_exemode", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_EXEMODE), 0, 0, 0);
   FileClose(h);
  }

bool ReadInputs()
  {
   int h = FileOpen("probe_cases.csv", FILE_READ | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE) { Print("probe: no probe_cases.csv"); return false; }
   for(int k = 0; k < 6; k++) FileReadString(h);                      // header id,dir,sl,tp,open_from,open_to
   while(!FileIsEnding(h))
     {
      string sid = FileReadString(h);
      if(sid == "") break;
      int n = ArraySize(C);
      ArrayResize(C, n + 1);
      C[n].id = StringToInteger(sid);
      C[n].dir = (int)StringToInteger(FileReadString(h));
      C[n].sl = StringToDouble(FileReadString(h));
      C[n].tp = StringToDouble(FileReadString(h));
      C[n].openFrom = StringToInteger(FileReadString(h));
      C[n].openTo = StringToInteger(FileReadString(h));
      C[n].tried = false; C[n].attempts = 0; C[n].opened = false; C[n].closed = false; C[n].pos = 0;
     }
   FileClose(h);
   h = FileOpen("probe_windows.csv", FILE_READ | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE) { Print("probe: no probe_windows.csv"); return false; }
   FileReadString(h); FileReadString(h);                                // header start_ms,end_ms
   while(!FileIsEnding(h))
     {
      string a = FileReadString(h);
      if(a == "") break;
      int n = ArraySize(W0);
      ArrayResize(W0, n + 1); ArrayResize(W1, n + 1);
      W0[n] = StringToInteger(a);
      W1[n] = StringToInteger(FileReadString(h));
     }
   FileClose(h);
   for(int i = 0; i < ArraySize(C); i++) if(C[i].openFrom < nextOpenMs) nextOpenMs = C[i].openFrom;
   PrintFormat("probe: %d cases, %d tick windows", ArraySize(C), ArraySize(W0));
   return true;
  }

int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER)) { Print("probe: tester only"); return INIT_FAILED; }
   WriteSessions();
   if(!ReadInputs()) return INIT_FAILED;
   hTicks  = FileOpen(Out("ticks"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   FileWrite(hTicks, "tick_msc", "bid", "ask", "flags");
   hOrders = FileOpen(Out("orders"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   FileWrite(hOrders, "probe", "tick_msc", "bid", "ask", "gap_s", "retcode", "price", "position");
   hRep    = FileOpen(Out("replica"), FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   FileWrite(hRep, "case_id", "event", "tick_msc", "bid", "ask", "retcode", "price", "reason", "position");
   trade.SetDeviationInPoints(1000);
   trade.SetTypeFillingBySymbol(_Symbol);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(hTicks != INVALID_HANDLE) FileClose(hTicks);
   if(hOrders != INVALID_HANDLE) FileClose(hOrders);
   if(hRep != INVALID_HANDLE) FileClose(hRep);
  }

void Probe(string kind, const MqlTick &tk, long gap)
  {
   trade.SetExpertMagicNumber(1);
   bool ok = trade.Buy(0.01, _Symbol, tk.ask, 0, 0, "probe");
   uint rc = trade.ResultRetcode();
   ulong pos = ok ? trade.ResultOrder() : 0;
   FileWrite(hOrders, kind, tk.time_msc, DoubleToString(tk.bid, _Digits), DoubleToString(tk.ask, _Digits), gap / 1000,
             rc, DoubleToString(trade.ResultPrice(), _Digits), (long)pos);
   if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED)) probePos = pos;
  }

void OnTick()
  {
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return;
   long ms = tk.time_msc;
   // 2. tick windows
   while(wk < ArraySize(W0) && ms > W1[wk]) wk++;
   if(wk < ArraySize(W0) && ms >= W0[wk])
      FileWrite(hTicks, ms, DoubleToString(tk.bid, _Digits), DoubleToString(tk.ask, _Digits), (long)tk.flags);
   // 3. order acceptance after a quote gap (> 5 min) and on the next minute's first tick
   if(probePos > 0 && PositionSelectByTicket(probePos))
     {
      if(trade.PositionClose(probePos)) probePos = 0;
     }
   long gap = (lastMs > 0) ? ms - lastMs : 0;
   if(gap > 300000)
     {
      openMin = ms / 60000;
      probedNext = false;
      Probe("open_first_tick", tk, gap);
     }
   else if(!probedNext && openMin >= 0 && ms / 60000 == openMin + 1)
     {
      probedNext = true;
      Probe("next_minute_first_tick", tk, 0);
     }
   lastMs = ms;
   // 4. replicas: open in the last bar before the gap
   if(ms < nextOpenMs) return;
   nextOpenMs = LONG_MAX;
   for(int i = 0; i < ArraySize(C); i++)
     {
      if(C[i].opened || C[i].attempts >= 3 || (C[i].tried && ms >= C[i].openTo)) continue;
      if(ms >= C[i].openTo) { C[i].tried = true; FileWrite(hRep, C[i].id, "not_opened", ms, "", "", 0, "", "", 0); continue; }
      if(ms < C[i].openFrom) { if(C[i].openFrom < nextOpenMs) nextOpenMs = C[i].openFrom; continue; }
      nextOpenMs = ms;                                // stay active while any replica waits in its bar
      bool inside = (C[i].dir == 1) ? (tk.bid > C[i].sl && tk.bid < C[i].tp) : (tk.ask < C[i].sl && tk.ask > C[i].tp);
      if(!inside) continue;
      trade.SetExpertMagicNumber(500000 + i);
      bool ok = (C[i].dir == 1) ? trade.Buy(0.01, _Symbol, tk.ask, C[i].sl, C[i].tp, "replica")
                                : trade.Sell(0.01, _Symbol, tk.bid, C[i].sl, C[i].tp, "replica");
      uint rc = trade.ResultRetcode();
      C[i].tried = true;
      C[i].attempts++;
      if(ok && rc == TRADE_RETCODE_DONE)
        {
         C[i].opened = true;
         C[i].pos = trade.ResultOrder();
        }
      FileWrite(hRep, C[i].id, "open", ms, DoubleToString(tk.bid, _Digits), DoubleToString(tk.ask, _Digits), rc,
                DoubleToString(trade.ResultPrice(), _Digits), "", (long)C[i].pos);
     }
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &req, const MqlTradeResult &res)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;
   long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
   ulong pos = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   for(int i = 0; i < ArraySize(C); i++)
     {
      if(!C[i].opened || C[i].closed || C[i].pos != pos) continue;
      C[i].closed = true;
      MqlTick tk;
      SymbolInfoTick(_Symbol, tk);
      FileWrite(hRep, C[i].id, "close", HistoryDealGetInteger(trans.deal, DEAL_TIME_MSC), DoubleToString(tk.bid, _Digits),
                DoubleToString(tk.ask, _Digits), 0, DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_PRICE), _Digits),
                EnumToString((ENUM_DEAL_REASON)HistoryDealGetInteger(trans.deal, DEAL_REASON)), (long)pos);
     }
  }
