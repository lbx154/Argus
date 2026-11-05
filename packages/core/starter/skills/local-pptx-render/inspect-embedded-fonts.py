#!/usr/bin/env python3
"""Inspect PPTX font relationships/EOT payloads without assuming the font name embeds it.
Usage: python3 inspect-embedded-fonts.py file.pptx
Reports structure only; does not certify target PowerPoint behavior or font licensing.
"""
import sys,zipfile,struct,xml.etree.ElementTree as ET,json,posixpath
P='http://schemas.openxmlformats.org/presentationml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
with zipfile.ZipFile(sys.argv[1]) as z:
 pres=ET.fromstring(z.read('ppt/presentation.xml'))
 rels={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))}
 result={'embedTrueTypeFonts':pres.get('embedTrueTypeFonts'),'saveSubsetFonts':pres.get('saveSubsetFonts'),'fonts':[]}
 for entry in pres.findall(f'{{{P}}}embeddedFontLst/{{{P}}}embeddedFont'):
  family=entry.find(f'{{{P}}}font').get('typeface')
  for style in ['regular','bold','italic','boldItalic']:
   part=entry.find(f'{{{P}}}{style}')
   if part is None:continue
   target=rels[part.get(f'{{{R}}}id')];path=posixpath.normpath(posixpath.join('ppt',target))
   b=z.read(path);total,size,version,flags=struct.unpack_from('<4I',b)
   fsType,magic=struct.unpack_from('<HH',b,32)
   assert total==len(b) and size<=len(b) and magic==0x504c,(path,'invalid EOT header')
   result['fonts'].append({'family':family,'style':style,'part':path,'bytes':len(b),'ttf_bytes':size,'version':hex(version),'flags':flags,'fsType':fsType,'sfnt_signature':b[-size:][:4].hex()})
 print(json.dumps(result,ensure_ascii=False,indent=2))
 assert result['fonts'],'No actual embedded font parts found'
