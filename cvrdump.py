#!/usr/bin/env python3
#pip install pypng

from struct import unpack
import argparse
import png
import sys
import os

def mkdir (path):
	try:
		os.mkdir (path)
	except FileExistsError:
		return False
	return True

def verify (cond, msg):
	if not cond:
		raise Exception (msg)

def pvr_decode (data):
	#Some PVR constants
	HEADER_SIZE = 16
	CODEBOOK_SIZE = 2048
	
	#Image must be one of these
	ARGB1555 = 0x0
	RGB565   = 0x1
	ARGB4444 = 0x2
	YUV422   = 0x3
	BUMP     = 0x4
	PAL_4BPP = 0x5
	PAL_8BPP = 0x6
	
	#And one of these
	SQUARE_TWIDDLED            = 0x1
	SQUARE_TWIDDLED_MIPMAP     = 0x2
	VQ                         = 0x3
	VQ_MIPMAP                  = 0x4
	CLUT_TWIDDLED_8BIT         = 0x5
	CLUT_TWIDDLED_4BIT         = 0x6
	DIRECT_TWIDDLED_8BIT       = 0x7
	DIRECT_TWIDDLED_4BIT       = 0x8
	RECTANGLE                  = 0x9
	RECTANGULAR_STRIDE         = 0xd
	SMALL_VQ                   = 0x10
	SMALL_VQ_MIPMAP            = 0x11
	SQUARE_TWIDDLED_MIPMAP_ALT = 0x12
	
	#For printing the above
	TYPES = [
		'ARGB1555',
		'RGB565',
		'ARGB4444',
		'YUV422',
		'BUMP',
		'4BPP',
		'8BPP'
	]
	FMTS = [
		'UNK0',
		'SQUARE TWIDDLED',
		'SQUARE TWIDDLED MIPMAP',
		'VQ',
		'VQ MIPMAP',
		'CLUT TWIDDLED 8BIT',
		'CLUT TWIDDLED 4BIT',
		'DIRECT TWIDDLED 8BIT',
		'DIRECT TWIDDLED 4BIT',
		'RECTANGLE',
		'UNK1',
		'UNK2',
		'UNK3',
		'RECTANGULAR STRIDE',
		'SMALL VQ',
		'SMALL VQ MIPMAP',
		'SQUARE TWIDDLED MIPMAP ALT'
	]
	
	#Ensure the texture is PVR encoded
	if data[:4].decode ('ASCII', 'ignore') != 'PVRT':
		return 'Not a PVR texture!', ''
	
	#Extract header
	px, fmt, unk, width, height = unpack ('<BBHHH', data[8:HEADER_SIZE])

	#Print info and verify
	print (f'    Type: {TYPES[px]} {FMTS[fmt]}, Size: {width}x{height}')
	verify (width < 0x8000, f'width is {width}; must be < {2^16}')
	verify (height < 0x8000, f'height is {height}; must be < {2^16}')
	
	#This is my favourite black magic spell!
	#Interleaves x and y to produce a morton code
	#This trivialises decoding PVR images
	def morton (x, y):
		x = (x|(x<<8))&0x00ff00ff
		y = (y|(y<<8))&0x00ff00ff
		x = (x|(x<<4))&0x0f0f0f0f
		y = (y|(y<<4))&0x0f0f0f0f
		x = (x|(x<<2))&0x33333333
		y = (y|(y<<2))&0x33333333
		x = (x|(x<<1))&0x55555555	
		y = (y|(y<<1))&0x55555555
		return x|(y<<1)
	
	#Colour decoders...
	def unpack1555 (colour):
		a = int (255*((colour>>15)&31))
		r = int (255*((colour>>10)&31)/31.0)
		g = int (255*((colour>> 5)&31)/31.0)
		b = int (255*((colour    )&31)/31.0)
		return [r, g, b, a]
		
	def unpack4444 (colour):
		a = int (255*((colour>>12)&15)/15.0)
		r = int (255*((colour>> 8)&15)/15.0)
		g = int (255*((colour>> 4)&15)/15.0)
		b = int (255*((colour    )&15)/15.0)
		return [r, g, b, a]
	
	def unpack565 (colour):
		r = int (255*((colour>>11)&31)/31.0)
		g = int (255*((colour>> 5)&63)/63.0)
		b = int (255*((colour    )&31)/31.0)
		return [r, g, b]
	
	#Format decoders...
	#GOTCHA: PVR stores mipmaps from smallest to largest!
	def vq_decode (raw, decoder):
		pix = []
		
		#Extract the codebook
		tmp = raw[HEADER_SIZE:]
		book = unpack (f'<1024H', tmp[:CODEBOOK_SIZE])
		
		#Skip to the largest mipmap
		#NB: This also avoids another gotcha:
		#Between the codebook and the mipmap data is a padding byte
		#Since we only want the largest though, it doesn't affect us
		#There is 10 byte padding at the end of VQ'd images
		size = len (raw) - 10
		base = width*height//4
		lut = raw[size - base : size]
		
		#The codebook is a 2x2 block of 16 bit pixels
		#This effectively halves the image dimensions
		#Each index of the data refers to a codebook entry
		for i in range (height//2):
			row0 = []
			row1 = []
			for j in range (width//2):
				entry = 4*lut[morton (i, j)]
				row0.extend (decoder (book[entry + 0]))
				row1.extend (decoder (book[entry + 1]))
				row0.extend (decoder (book[entry + 2]))
				row1.extend (decoder (book[entry + 3]))
			pix.append (row0)
			pix.append (row1)
		return pix
	
	def morton_decode (raw, decoder):
		pix = []
		
		#Skip to largest mipmap
		size = len (raw)
		base = width*height*2
		mip = raw[size - base : size]
		
		data = unpack (f'<{width*height}H', mip)
		for i in range (height):
			row = []
			for j in range (width):
				row.extend (decoder (data[morton (i, j)]))
			pix.append (row)
		return pix
	
	#From observation:
	#All textures 16 bit
	#All textures are either VQ'd or morton coded (twiddled)
	#So let's just save time and only implement those
	if ARGB1555 == px:
		if SQUARE_TWIDDLED == fmt or SQUARE_TWIDDLED_MIPMAP == fmt:
			return morton_decode (data, unpack1555), 'RGBA'
		elif VQ == fmt or VQ_MIPMAP:
			return vq_decode (data, unpack1555), 'RGBA'
	elif ARGB4444 == px:
		if SQUARE_TWIDDLED == fmt or SQUARE_TWIDDLED_MIPMAP == fmt:
			return morton_decode (data, unpack4444), 'RGBA'
		elif VQ == fmt or VQ_MIPMAP:
			return vq_decode (data, unpack4444), 'RGBA'
	elif RGB565 == px:
		if SQUARE_TWIDDLED == fmt or SQUARE_TWIDDLED_MIPMAP == fmt:
			return morton_decode (data, unpack565), 'RGB'
		elif VQ == fmt or VQ_MIPMAP:
			return vq_decode (data, unpack565), 'RGB'
	
	#Oh, well...
	return 'Unsupported encoding', ''
	
def readbin (path, prefix, raw):
	#.bin writes everything out in 2k pages
	ALIGNMENT = 2048
	
	#Create prefix directory where to store everything
	mkdir (prefix)
	
	#Try to open a stream to the blob
	try:
		f = open (path, 'rb')
	except FileNotFoundError:
		print (f'Could not open stream to "{path}"; ignoring...')
		return
	
	file_length = os.fstat (f.fileno ()).st_size
	print (f'Reading "{path}"...')
	print (f'Length: {file_length}')
	
	#There seems to be no directory count, so in order to read
	#all the directories we have to go until the end of file
	while f.tell () < file_length:
		unk0, unk1 = unpack ('<II', f.read (8))
		dn = f.read (32).decode ('ASCII', 'ignore')[:-1]
		print (f'Reading directory "{dn}"...')
		
		nfiles = unpack ('<I', f.read (4))[0]
		print (f'  Files: {nfiles}')
		print (f'  Unk0: {unk0}')
		print (f'  Unk1: {unk1}')
		
		mkdir (os.path.join (prefix, dn))
		
		for i in range (nfiles):
			#Read the file header...
			fn = f.read (32).decode ('ASCII', 'ignore')[:-1]
			sz, unk3 = unpack ('<II', f.read (8))
			fp = f.tell ()
			print (f'  Reading file "{fn}" ({sz}) ({unk3}) @ {fp} ({i})...')
			
			#Now for the file contents...
			op = os.path.join (prefix, dn, fn)
			data = f.read (sz)
			
			#Just store it raw and continue if that's what the user wants
			if True == raw:
				with open (op, 'wb') as cont:
					cont.write (data)
			else:	
				#Perform any conversions
				#The directories are mostly dedicated to single file types,
				#so it's a good enough discriminator as far as I'm concerned
				if 'textures' == dn.lower ():
					ret, mode = pvr_decode (data)
					verify (str != type (ret), f'image {fn} failed to decode: {ret}!')	
					png.from_array (ret, mode).save (op + '.png')
				else:
					#Dump raw contents for unprocessed files
					with open (op, 'wb') as cont:
						cont.write (data)
			
			#Skip to the next page boundary
			f.seek ((f.tell () + (ALIGNMENT - 1))&~(ALIGNMENT - 1))
			
	f.close ()

def main ():
	p = argparse.ArgumentParser (description = 'Extracts Castlevania: Resurrection .bin file contents')
	p.add_argument ('files', metavar='file', type=str, nargs='+', help='A .bin file to extract')
	p.add_argument ('--prefix', type=str, default='contents', help='Path to store the assets')
	p.add_argument ('--raw', default='False', action=argparse.BooleanOptionalAction, help='Disables asset preprocessing')
	
	args = p.parse_args ()
	for a in args.files:
		readbin (a, args.prefix, args.raw)

if __name__ == "__main__":
	main ()
