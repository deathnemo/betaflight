#!/usr/bin/python

# Written by Antonio Galea - 2010/11/18
# Distributed under Gnu LGPL 3.0
# see http://www.gnu.org/licenses/lgpl-3.0.txt
#
# based on a modified version of this script from https://sourceforge.net/p/dfu-util/tickets/35/#357c
# with the patch supplied in https://sourceforge.net/p/dfu-util/tickets/35/#a2b6

import sys,struct,zlib,os
import binascii
from optparse import OptionParser


def _ihex_segments(path):
    """
    Разбор Intel HEX без пакета intelhex (PEP 668 / MSYS2 — pip в системный Python часто недоступен).
    Записи 00 (data), 01 (EOF), 04 (extended linear address); остальное игнорируем.
    """
    mem = {}
    upper = 0
    with open(path, "r", encoding="ascii", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line.startswith(":"):
                continue
            line = line[1:]
            if len(line) < 8:
                continue
            try:
                nbytes = int(line[0:2], 16)
                addr_lo = int(line[2:6], 16)
                rtype = int(line[6:8], 16)
                need = 8 + nbytes * 2 + 2
                if len(line) < need:
                    continue
                payload_hex = line[8 : 8 + nbytes * 2]
            except ValueError:
                continue
            if rtype == 0x00:
                try:
                    chunk = bytes.fromhex(payload_hex)
                except ValueError:
                    continue
                base = (upper + addr_lo) & 0xFFFFFFFF
                for i, byte in enumerate(chunk):
                    mem[(base + i) & 0xFFFFFFFF] = byte
            elif rtype == 0x01:
                break
            elif rtype == 0x04 and nbytes == 2:
                try:
                    upper = int(payload_hex, 16) << 16
                except ValueError:
                    pass
    if not mem:
        return []
    addrs = sorted(mem.keys())
    target = []
    seg_lo = addrs[0]
    prev = addrs[0]
    for a in addrs[1:]:
        if a == prev + 1:
            prev = a
        else:
            data = bytes(mem[i] for i in range(seg_lo, prev + 1))
            target.append({"address": seg_lo & 0xFFFFFFFF, "data": data})
            seg_lo = prev = a
    data = bytes(mem[i] for i in range(seg_lo, prev + 1))
    target.append({"address": seg_lo & 0xFFFFFFFF, "data": data})
    return target

DEFAULT_DEVICE="0x0483:0xdf11"
DEFAULT_NAME=b'ST...'

def named(tuple,names):
  return dict(list(zip(names.split(),tuple)))
def consume(fmt,data,names):
  n = struct.calcsize(fmt)
  return named(struct.unpack(fmt,data[:n]),names),data[n:]
def cstring(bytestring):
  return bytestring.partition(b'\0')[0]
def compute_crc(data):
  return 0xFFFFFFFF & -zlib.crc32(data) -1

def parse(file,dump_images=False):
  print('File: "%s"' % file)
  data = open(file,'rb').read()
  crc = compute_crc(data[:-4])
  prefix, data = consume('<5sBIB',data,'signature version size targets')
  print('%(signature)s v%(version)d, image size: %(size)d, targets: %(targets)d' % prefix)
  for t in range(prefix['targets']):
    tprefix, data  = consume('<6sBI255s2I',data,'signature altsetting named name size elements')
    tprefix['num'] = t
    if tprefix['named']:
      tprefix['name'] = cstring(tprefix['name'])
    else:
      tprefix['name'] = ''
    print('%(signature)s %(num)d, alt setting: %(altsetting)s, name: "%(name)s", size: %(size)d, elements: %(elements)d' % tprefix)
    tsize = tprefix['size']
    target, data = data[:tsize], data[tsize:]
    for e in range(tprefix['elements']):
      eprefix, target = consume('<2I',target,'address size')
      eprefix['num'] = e
      print('  %(num)d, address: 0x%(address)08x, size: %(size)d' % eprefix)
      esize = eprefix['size']
      image, target = target[:esize], target[esize:]
      if dump_images:
        out = '%s.target%d.image%d.bin' % (file,t,e)
        open(out,'wb').write(image)
        print('    DUMPED IMAGE TO "%s"' % out)
    if len(target):
      print("target %d: PARSE ERROR" % t)
  suffix = named(struct.unpack('<4H3sBI',data[:16]),'device product vendor dfu ufd len crc')
  print('usb: %(vendor)04x:%(product)04x, device: 0x%(device)04x, dfu: 0x%(dfu)04x, %(ufd)s, %(len)d, 0x%(crc)08x' % suffix)
  if crc != suffix['crc']:
    print("CRC ERROR: computed crc32 is 0x%08x" % crc)
  data = data[16:]
  if data:
    print("PARSE ERROR")

def checkbin(binfile):
  data = open(binfile,'rb').read()
  if (len(data) < 16):
    return
  crc = compute_crc(data[:-4])
  suffix = named(struct.unpack('<4H3sBI',data[-16:]),'device product vendor dfu ufd len crc')
  if crc == suffix['crc'] and suffix['ufd'] == b'UFD':
    print('usb: %(vendor)04x:%(product)04x, device: 0x%(device)04x, dfu: 0x%(dfu)04x, %(ufd)s, %(len)d, 0x%(crc)08x' % suffix)
    print("It looks like the file %s has a DFU suffix!" % binfile)
    print("Please remove any DFU suffix and retry.")
    sys.exit(1)

def build(file,targets,name=DEFAULT_NAME,device=DEFAULT_DEVICE):
  data = b''
  for t,target in enumerate(targets):
    tdata = b''
    for image in target:
      tdata += struct.pack('<2I',image['address'],len(image['data']))+image['data']
    tdata = struct.pack('<6sBI255s2I',b'Target',0,1,name,len(tdata),len(target)) + tdata
    data += tdata
  data  = struct.pack('<5sBIB',b'DfuSe',1,len(data)+11,len(targets)) + data
  v,d=[int(x,0) & 0xFFFF for x in device.split(':',1)]
  data += struct.pack('<4H3sB',0x2200,d,v,0x011a,b'UFD',16)
  crc   = compute_crc(data)
  data += struct.pack('<I',crc)
  open(file,'wb').write(data)

if __name__=="__main__":
  usage = """
%prog [-d|--dump] infile.dfu
%prog {-b|--build} address:file.bin [-b address:file.bin ...] [{-D|--device}=vendor:device] outfile.dfu
%prog {-s|--build-s19} file.s19 [{-D|--device}=vendor:device] outfile.dfu
%prog {-i|--ihex} file.hex [-i file.hex ...] [{-D|--device}=vendor:device] outfile.dfu"""
  parser = OptionParser(usage=usage)
  parser.add_option("-b", "--build", action="append", dest="binfiles",
    help="build a DFU file from given BINFILES. Note that the BINFILES must not have any DFU suffix!", metavar="BINFILES")
  parser.add_option("-i", "--ihex", action="append", dest="hexfiles",
    help="build a DFU file from given HEXFILES", metavar="HEXFILES")
  parser.add_option("-s", "--build-s19", type="string", dest="s19files",
    help="build a DFU file from given S19 S-record file.", metavar="S19FILE")
  parser.add_option("-D", "--device", action="store", dest="device",
    help="build for DEVICE, defaults to %s" % DEFAULT_DEVICE, metavar="DEVICE")
  parser.add_option("-d", "--dump", action="store_true", dest="dump_images",
    default=False, help="dump contained images to current directory")
  (options, args) = parser.parse_args()

  if (options.binfiles or options.hexfiles) and len(args)==1:
    target = []

    if options.binfiles:
      for arg in options.binfiles:
        try:
          address,binfile = arg.split(':',1)
        except ValueError:
          print("Address:file couple '%s' invalid." % arg)
          sys.exit(1)
        try:
          address = int(address,0) & 0xFFFFFFFF
        except ValueError:
          print("Address %s invalid." % address)
          sys.exit(1)
        if not os.path.isfile(binfile):
          print("Unreadable file '%s'." % binfile)
          sys.exit(1)
        checkbin(binfile)
        target.append({ 'address': address, 'data': open(binfile,'rb').read() })

    if options.hexfiles:
      for hexfile in options.hexfiles:
        for seg in _ihex_segments(hexfile):
          target.append(seg)

    outfile = args[0]
    device = DEFAULT_DEVICE
    if options.device:
      device=options.device
    try:
      v,d=[int(x,0) & 0xFFFF for x in device.split(':',1)]
    except:
      print("Invalid device '%s'." % device)
      sys.exit(1)
    build(outfile,[target],DEFAULT_NAME,device)
  elif options.s19files and len(args)==1:
    address = 0
    data = ""
    target = []
    name = DEFAULT_NAME
    with open(options.s19files) as f:
      lines = f.readlines()
      for line in lines:
          curaddress = 0
          curdata = ""
          line = line.rstrip()
          if line.startswith ( "S0" ):
            name = binascii.a2b_hex(line[8:len(line) - 2]).replace(".s19", "")
          elif line.startswith ( "S3" ):
            try:
              curaddress = int(line[4:12], 16) & 0xFFFFFFFF
            except ValueError:
              print("Address %s invalid." % address)
              sys.exit(1)
            curdata = binascii.unhexlify(line[12:-2])
          elif line.startswith ( "S2" ):
            try:
              curaddress = int(line[4:10], 16) & 0xFFFFFFFF
            except ValueError:
              print("Address %s invalid." % address)
              sys.exit(1)
            curdata = binascii.unhexlify(line[10:-2])
          elif line.startswith ( "S1" ):
            try:
              curaddress = int(line[4:8], 16) & 0xFFFFFFFF
            except ValueError:
              print("Address %s invalid." % address)
              sys.exit(1)
            curdata = binascii.unhexlify(line[8:-2])
          if address == 0:
              address = curaddress
              data = curdata
          elif address + len(data) != curaddress:
              target.append({ 'address': address, 'data': data })
              address = curaddress
              data = curdata
          else:
              data += curdata
    outfile = args[0]
    device = DEFAULT_DEVICE
    if options.device:
      device=options.device
    try:
      v,d=[int(x,0) & 0xFFFF for x in device.split(':',1)]
    except:
      print("Invalid device '%s'." % device)
      sys.exit(1)
    build(outfile,[target],name,device)
  elif len(args)==1:
    infile = args[0]
    if not os.path.isfile(infile):
      print("Unreadable file '%s'." % infile)
      sys.exit(1)
    parse(infile, dump_images=options.dump_images)
  else:
    parser.print_help()
    sys.exit(1)
