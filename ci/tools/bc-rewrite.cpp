#include "llvm/Bitcode/BitcodeReader.h"
#include "llvm/Bitcode/BitcodeWriter.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"
using namespace llvm;
// rw in out uselist(0/1) hash(0/1)
int main(int c, char **v) {
  LLVMContext ctx;
  auto buf = MemoryBuffer::getFile(v[1]);
  auto m = parseBitcodeFile((*buf)->getMemBufferRef(), ctx);
  if (!m) { errs() << "parse fail\n"; return 1; }
  std::error_code ec; raw_fd_ostream os(v[2], ec);
  WriteBitcodeToFile(**m, os, v[3][0] == '1', nullptr, v[4][0] == '1');
}
