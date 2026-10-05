//+------------------------------------------------------------------+
//| duka_import.mq5 - Dukascopy XAUUSD data check (2026-10-05)        |
//| Script for the isolated copy only (C:\mt5r, /portable, offline):  |
//| creates the custom symbol and loads the January 2024 and March    |
//| 2026 ticks, then reads them back for comparison. It contains no   |
//| trading function and refuses to run when the terminal is          |
//| connected, when trading is allowed, or inside the Tester.         |
//+------------------------------------------------------------------+
#property script_show_inputs false
#property strict

input string CustomName = "XAUUSD.duka";
input string TickFiles  = "duka_XAUUSD_2024-01.bin;duka_XAUUSD_2026-03.bin";

int gLog = INVALID_HANDLE;

void Log(string s)
  {
   Print("duka_import: ", s);
   if(gLog != INVALID_HANDLE) FileWriteString(gLog, s + "\r\n");
  }

// the specification is set explicitly from the data (3 decimals, 0.001 tick); nothing is copied from XAUUSD.s
bool SetSpec(string name)
  {
   bool ok = true;
   ok &= CustomSymbolSetString(name, SYMBOL_DESCRIPTION, "Dukascopy XAU/USD ticks, time in UTC (data check)");
   ok &= CustomSymbolSetString(name, SYMBOL_CURRENCY_BASE, "XAU");
   ok &= CustomSymbolSetString(name, SYMBOL_CURRENCY_PROFIT, "USD");
   ok &= CustomSymbolSetString(name, SYMBOL_CURRENCY_MARGIN, "USD");
   ok &= CustomSymbolSetInteger(name, SYMBOL_DIGITS, 3);
   ok &= CustomSymbolSetDouble(name, SYMBOL_POINT, 0.001);
   ok &= CustomSymbolSetDouble(name, SYMBOL_TRADE_TICK_SIZE, 0.001);
   ok &= CustomSymbolSetDouble(name, SYMBOL_TRADE_CONTRACT_SIZE, 100.0);
   ok &= CustomSymbolSetDouble(name, SYMBOL_TRADE_TICK_VALUE, 0.1);          // 0.001 x 100 oz, in USD
   ok &= CustomSymbolSetInteger(name, SYMBOL_TRADE_CALC_MODE, SYMBOL_CALC_MODE_CFD);
   ok &= CustomSymbolSetInteger(name, SYMBOL_SPREAD_FLOAT, true);
   ok &= CustomSymbolSetInteger(name, SYMBOL_CHART_MODE, SYMBOL_CHART_MODE_BID);
   ok &= CustomSymbolSetDouble(name, SYMBOL_VOLUME_MIN, 0.01);
   ok &= CustomSymbolSetDouble(name, SYMBOL_VOLUME_STEP, 0.01);
   ok &= CustomSymbolSetDouble(name, SYMBOL_VOLUME_MAX, 100.0);
   // sessions cover the whole week so that no tick is dropped by a session filter in this data check; the real
   // quote hours are measured from the data (gaps) and reported separately
   for(int d = 0; d < 7; d++)
     {
      ok &= CustomSymbolSetSessionQuote(name, (ENUM_DAY_OF_WEEK)d, 0, 0, 86400);
      ok &= CustomSymbolSetSessionTrade(name, (ENUM_DAY_OF_WEEK)d, 0, 0, 86400);
     }
   return ok;
  }

// file: int64 from_msc, int64 to_msc, int64 n, then n x (int64 time_msc, double bid, double ask)
bool LoadFile(string name, string file)
  {
   int h = FileOpen(file, FILE_READ | FILE_BIN);
   if(h == INVALID_HANDLE) { Log(file + ": cannot open, error " + IntegerToString(GetLastError())); return false; }
   long from = FileReadLong(h), to = FileReadLong(h), n = FileReadLong(h);
   MqlTick t[];
   if(ArrayResize(t, (int)n) != n) { Log(file + ": cannot allocate " + IntegerToString(n)); FileClose(h); return false; }
   for(long i = 0; i < n; i++)
     {
      ZeroMemory(t[i]);
      t[i].time_msc = FileReadLong(h);
      t[i].time     = (datetime)(t[i].time_msc / 1000);
      t[i].bid      = FileReadDouble(h);
      t[i].ask      = FileReadDouble(h);
      t[i].flags    = TICK_FLAG_BID | TICK_FLAG_ASK;
     }
   FileClose(h);
   ResetLastError();
   int added = CustomTicksReplace(name, from, to, t);
   Log(file + ": from_msc " + IntegerToString(from) + " to_msc " + IntegerToString(to) + " ticks_in_file " +
       IntegerToString(n) + " replaced " + IntegerToString(added) + " error " + IntegerToString(GetLastError()));
   // read back what the terminal stored, in the same layout, for the comparison with the source
   MqlTick back[];
   int got = CopyTicksRange(name, back, COPY_TICKS_ALL, (ulong)from, (ulong)to);
   string out = "readback_" + file;
   int w = FileOpen(out, FILE_WRITE | FILE_BIN);
   if(w == INVALID_HANDLE) { Log(out + ": cannot write"); return false; }
   FileWriteLong(w, from); FileWriteLong(w, to); FileWriteLong(w, MathMax(got, 0));
   for(int i = 0; i < got; i++)
     {
      FileWriteLong(w, back[i].time_msc);
      FileWriteDouble(w, back[i].bid);
      FileWriteDouble(w, back[i].ask);
     }
   FileClose(w);
   Log(file + ": readback " + IntegerToString(got) + " ticks -> " + out);
   return added == n && got == n;
  }

void OnStart()
  {
   gLog = FileOpen("duka_import_log.txt", FILE_WRITE | FILE_TXT | FILE_ANSI);
   // fail closed: this script runs only offline, outside the Tester, with trading switched off
   if(MQLInfoInteger(MQL_TESTER))                    { Log("refused: inside the Tester"); FileClose(gLog); return; }
   if(TerminalInfoInteger(TERMINAL_CONNECTED))       { Log("refused: terminal is connected to a server"); FileClose(gLog); return; }
   if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))   { Log("refused: algorithmic trading is allowed"); FileClose(gLog); return; }
   Log("offline, trading off: importing into " + CustomName);
   ResetLastError();
   if(!CustomSymbolCreate(CustomName, "Dukascopy", NULL))
     {
      int e = GetLastError();
      if(e != ERR_CUSTOM_SYMBOL_EXIST) { Log("CustomSymbolCreate failed, error " + IntegerToString(e)); FileClose(gLog); return; }
      Log("custom symbol exists: its ticks in each file's range are replaced");
     }
   Log("spec set: " + (SetSpec(CustomName) ? "ok" : "FAILED"));
   string files[];
   int k = StringSplit(TickFiles, ';', files);
   bool all = true;
   for(int i = 0; i < k; i++) all &= LoadFile(CustomName, files[i]);
   Log(all ? "result: ok" : "result: MISMATCH");
   FileClose(gLog);
  }
