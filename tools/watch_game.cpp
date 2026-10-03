#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dbghelp.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <initializer_list>

volatile LONG stop_requested=0;
BOOL WINAPI Stop(DWORD event) {
    if(event==CTRL_C_EVENT || event==CTRL_BREAK_EVENT) {
        InterlockedExchange(&stop_requested,1);
        return TRUE;
    }
    return FALSE;
}

void Report(HANDLE process, DWORD thread_id, const EXCEPTION_DEBUG_INFO& e) {
    auto& record=e.ExceptionRecord;
    std::printf("Exception %08lX at %p, thread %lu, first chance %lu\n", record.ExceptionCode, record.ExceptionAddress, thread_id, e.dwFirstChance);
    for (DWORD i=0;i<record.NumberParameters;++i) std::printf("  parameter %lu: %llX\n",i,(unsigned long long)record.ExceptionInformation[i]);
    HANDLE thread=OpenThread(THREAD_GET_CONTEXT|THREAD_QUERY_INFORMATION,FALSE,thread_id);
    CONTEXT context{}; context.ContextFlags=CONTEXT_ALL;
    if (!GetThreadContext(thread,&context)) {CloseHandle(thread);return;}
    auto original=context;
    STACKFRAME64 frame{};
    // ADDRESS64 also contains Segment between Offset and Mode. A two-value
    // aggregate initializer leaves Mode at AddrMode1616, which breaks x64 walks.
    frame.AddrPC.Offset=context.Rip; frame.AddrPC.Mode=AddrModeFlat;
    frame.AddrStack.Offset=context.Rsp; frame.AddrStack.Mode=AddrModeFlat;
    frame.AddrFrame.Offset=context.Rbp; frame.AddrFrame.Mode=AddrModeFlat;
    for(int i=0;i<48;++i){
        char buffer[sizeof(SYMBOL_INFO)+MAX_SYM_NAME]{};
        auto* sym=reinterpret_cast<SYMBOL_INFO*>(buffer); sym->SizeOfStruct=sizeof(SYMBOL_INFO);sym->MaxNameLen=MAX_SYM_NAME;
        DWORD64 displacement=0;
        if(SymFromAddr(process,frame.AddrPC.Offset,&displacement,sym)) std::printf("  %llX %s+%llX\n",frame.AddrPC.Offset,sym->Name,displacement);
        else std::printf("  %llX\n",frame.AddrPC.Offset);
        if(!StackWalk64(IMAGE_FILE_MACHINE_AMD64,process,thread,&frame,&context,nullptr,SymFunctionTableAccess64,SymGetModuleBase64,nullptr))break;
    }
    // PPCContext frequently lives in RCX or RSI, with 32 64-bit GPRs after
    // its kernel-state pointer. Dump candidates for guest object tracing.
    for (auto address : {original.Rcx, original.Rsi}) {
        uint64_t gpr[35]{}; SIZE_T count=0;
        if(!ReadProcessMemory(process,reinterpret_cast<void*>(address),gpr,sizeof(gpr),&count)) continue;
        std::printf("Context candidate %llX:",address);
        for(int i=0;i<35;++i)std::printf(" %llX",gpr[i]);
        std::puts("");
        for(int i : {1, 29, 30, 31, 32}) {
            if(gpr[i] < 0x10000 || gpr[i]>0xFFFFFFFF)continue;
            unsigned char bytes[192]{};
            if(!ReadProcessMemory(process,reinterpret_cast<void*>(0x100000000ull+gpr[i]),bytes,sizeof(bytes),&count))continue;
            std::printf("Guest object %llX:",gpr[i]);
            for(auto byte:bytes)std::printf("%02X",byte);
            std::puts("");
        }
    }
    CloseHandle(thread);
    std::fflush(stdout);
}
int main(int argc,char** argv) {
    if(argc!=2){std::puts("Usage: crackdown_debugger <game PID> (Ctrl+C detaches)");return 2;}
    DWORD pid=strtoul(argv[1],nullptr,10);
    if(!DebugActiveProcess(pid)){std::printf("Attach failed: %lu\n",GetLastError());return 1;}
    DebugSetProcessKillOnExit(FALSE);
    SetConsoleCtrlHandler(Stop,TRUE);
    HANDLE process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,pid);
    SymSetOptions(SYMOPT_UNDNAME|SYMOPT_DEFERRED_LOADS|SYMOPT_LOAD_LINES);
    if(!SymInitialize(process,nullptr,TRUE))std::printf("Symbol setup failed: %lu\n",GetLastError());
    std::printf("Attached to %lu\n",pid);std::fflush(stdout);
    DEBUG_EVENT event{};
    while(!InterlockedCompareExchange(&stop_requested,0,0)) {
        if(!WaitForDebugEvent(&event,1000)) {
            if(GetLastError()==ERROR_SEM_TIMEOUT)continue;
            break;
        }
        DWORD status=DBG_CONTINUE;
        if(event.dwDebugEventCode==EXCEPTION_DEBUG_EVENT) {
            auto& e=event.u.Exception;
            if(e.ExceptionRecord.ExceptionCode!=EXCEPTION_BREAKPOINT) {
                status=DBG_EXCEPTION_NOT_HANDLED;
                // First-chance GPU page-protection faults are handled by the
                // runtime and may occur thousands of times during normal play.
                if(!e.dwFirstChance)Report(process,event.dwThreadId,e);
            }
        }
        if(event.dwDebugEventCode==CREATE_PROCESS_DEBUG_EVENT && event.u.CreateProcessInfo.hFile)CloseHandle(event.u.CreateProcessInfo.hFile);
        if(event.dwDebugEventCode==CREATE_PROCESS_DEBUG_EVENT) {
            CloseHandle(event.u.CreateProcessInfo.hProcess);
            CloseHandle(event.u.CreateProcessInfo.hThread);
        }
        if(event.dwDebugEventCode==CREATE_THREAD_DEBUG_EVENT)CloseHandle(event.u.CreateThread.hThread);
        if(event.dwDebugEventCode==LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile)CloseHandle(event.u.LoadDll.hFile);
        ContinueDebugEvent(event.dwProcessId,event.dwThreadId,status);
        if(event.dwDebugEventCode==EXIT_PROCESS_DEBUG_EVENT){std::printf("Exited %08lX\n",event.u.ExitProcess.dwExitCode);break;}
    }
    DebugActiveProcessStop(pid);
    SymCleanup(process);CloseHandle(process);
    return 0;
}
