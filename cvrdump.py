#!/usr/bin/env python3
#pip install pypng

from struct import pack, unpack, calcsize
import collections
import argparse
import binascii
import json
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
		
def cstr_decode (data):
	zero = data.index (0)
	return data[:zero].decode ('ASCII').lower ()

#Keep track of assets by symbolic name
sskdb = {}
ssndb = {}
scfdb = {}
smtdb = {}

fspaths = []
def open_file (path, mode):
	global fspaths
	for pref in fspaths:
		try:
			return open (os.path.join (pref, path), mode)
		except:
			continue
	
	raise Exception (f'Could not open "{path}"!')

def pvr_decode (data):
	#Some PVR constants
	HEADER_SIZE = 16
	CODEBOOK_SIZE = 2048
	MAX_WIDTH = 0x8000
	MAX_HEIGHT = 0x8000
	
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
	verify (width < MAX_WIDTH, f'width is {width}; must be < {MAX_WIDTH}')
	verify (height < MAX_HEIGHT, f'height is {height}; must be < {MAX_HEIGHT}')
	
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

def smt_load (data):
	Params = collections.namedtuple ('Params', ['col0', 'col1', 'col2', 'col3'])
	
	#A couple of materials are corrupt :(
	if len (data) < 120:
		return 'Malformed material!', '', ''
	
	tag = cstr_decode (data[:32])
	tmp = data[32:]
	
	count = unpack ('<I', tmp[:4])[0]
	tmp = tmp[4:]
	
	params = []
	for i in range (count):
		PARAM_FMT = '<I3f3f3f3f'
		PARAM_SIZE = calcsize (PARAM_FMT)
		
		unk0,\
		col0x, col0y, col0z,\
		col1x, col1y, col1z,\
		col2x, col2y, col2z,\
		col3x, col3y, col3z = unpack (PARAM_FMT, tmp[:PARAM_SIZE])
		
		params.append (Params (\
				  [col0x, col0y, col0z],\
				  [col1x, col1y, col1z],\
				  [col2x, col2y, col2z],\
				  [col3x, col3y, col3z]))
	
		tmp = tmp[PARAM_SIZE:]
	
	tags = []
	for i in range (count):
		tags.append (cstr_decode (tmp[:32]))
		tmp = tmp[32:]
	
	return tags, params, count

def ssk_symbolic_name (data):
	FMT = '<5I32s'
	SIZE = calcsize (FMT)
	size, unk0, nbones, unk1, unk2, tag = unpack (FMT, data[:SIZE])
	return cstr_decode (tag)
	
def ssk_load (data):
	FMT = '<5I32s'
	SIZE = calcsize (FMT)
	BONE_FMT = '<32sI21f96sII'
	BONE_SIZE = calcsize (BONE_FMT)
	Bone = collections.namedtuple ('Bone', ['tag', 'index', 'children', 'rows'])
	size, unk0, nbones, unk1, unk2, tag = unpack (FMT, data[:SIZE])

	bl = []
	bones = data[SIZE:]
	offset = bones[BONE_SIZE*nbones:]
	hierarchy = list (unpack (f'<{nbones - 1}I', offset[:calcsize (f'<{nbones - 1}I')]))
	for i in range (nbones):
		tag, index,\
		m00, m01, m02, m03, m04, m05, m06, m07, m08, m09, m10,\
		m11, m12, m13, m14, m15, m16, m17, m18, m19, m20,\
		pad, nchildren, unk = unpack (BONE_FMT, bones[:BONE_SIZE])

		tag = cstr_decode (tag)

		#Slice off the relevant indices from the hierarchy list
		children = hierarchy[:nchildren]
		hierarchy = hierarchy[nchildren:]
		bl.append (Bone (tag, index, children, [
			[m00, m01, m02, m03],
			[m04, m05, m06, m07],
			[m08, m09, m10, m11],
			[m12, m13, m14, m15],
			[m16, m17, m18, m19],
			[m20]
		]))

		#Advance to the next entry
		bones = bones[BONE_SIZE:]
	
	return bl

def ssn_symbolic_name (data):
	HEADER_FMT = '<7I32s'
	HEADER_SIZE = calcsize (HEADER_FMT)
	
	size, unk0, count,\
	unk1, unk2, unk3, unk4,\
	tag = unpack (HEADER_FMT, data[:HEADER_SIZE])
	
	return cstr_decode (tag)
	
def ssn_load (data):
	HEADER_FMT = '<7I32s'
	HEADER_SIZE = calcsize (HEADER_FMT)
	Binding = collections.namedtuple ('Binding', ['bone', 'count', 'offset'])
	Multiplex = collections.namedtuple ('Multiplex', ['count', 'bones', 'bias'])
	
	size, unk0, count, multiplexed,\
	unk1, unk2, unk3,\
	tag = unpack (HEADER_FMT, data[:HEADER_SIZE])
	data = data[HEADER_SIZE:]
	
	book = []
	for i in range (count):
		BIND_SIZE = calcsize ('<3I')
		bone, count, offset = unpack ('<3I', data[:BIND_SIZE])
		
		book.append (Binding (bone, count, offset))
		data = data[BIND_SIZE:]
		
	mplx = []
	for i in range (multiplexed):
		MPLX_FMT = '<4I3f'
		MPLX_SIZE = calcsize (MPLX_FMT)
		
		count, b0, b1, b2, w0, w1, w2 = unpack (MPLX_FMT, data[:MPLX_SIZE])
		mplx.append (Multiplex (count, [b0, b1, b2], [w0, w1, w2]))
		data = data[MPLX_SIZE:]
		
		#Skip the offsets
		data = data[4*16:]
		
		
	return book, mplx

def smf_symbolic_name (data):
	HEADER_FMT = '<15I'
	HEADER_SIZE = calcsize (HEADER_FMT)
	
	size, unk0, magick,\
	unk1, unk2, unk3, unk4, unk5, unk6,\
	nverts,\
	unk7, unk8, unk9, unk10,\
	count = unpack (HEADER_FMT, data[:HEADER_SIZE])

	tmp = data[HEADER_SIZE + calcsize ('<54I'):]
	return cstr_decode (tmp[:32])

class GLTF:
	LINEAR = 0x2601
	LINEAR_MIPMAP_LINEAR  = 0x2703
	REPEAT = 0x8370
	FLOAT = 0x1406
	UNSIGNED_INT = 0x1405
	TRIANGLE_STRIP = 0x5

def smf_decode (data, prefix, filename, skel, gltf, matname):
	HEADER_FMT = '<15I'
	HEADER_SIZE = calcsize (HEADER_FMT)
	Strip = collections.namedtuple ('Strip', ['length', 'slot', 'index_offset', 'uv_offset'])
	
	#Generates armature if true
	scf = skel is not None
	
	#Buffer to store the binary data
	binfile = bytes ()
	
	#Get arrays for the gltf fields
	accessors = gltf['accessors'] if 'accessors' in gltf else []
	views = gltf['bufferViews'] if 'bufferViews' in gltf else []
	nodes = gltf['nodes'] if 'nodes' in gltf else []
	images = gltf['images'] if 'images' in gltf else []
	textures = gltf['textures'] if 'textures' in gltf else []
	materials = gltf['materials'] if 'materials' in gltf else []
	samplers = gltf['sampler'] if 'samplers' in gltf else []
	buffers = gltf['buffers'] if 'buffers' in gltf else []
	skins = gltf['skins'] if 'skins' in gltf else []
	
	#All textures seem to use the same sampler
	samplers.append ({
		'magFilter' : GLTF.LINEAR,
		'minFilter' : GLTF.LINEAR_MIPMAP_LINEAR,
		'wrapS': GLTF.REPEAT,
		'warpT': GLTF.REPEAT
	})
	
	#Fill out material/texture properties
	with open_file (os.path.join ('smt', matname), 'rb') as f:
		tags, params, count = smt_load (f.read ())
		if type (tags) == str:
			tags = 'ERROR'
			count = 1

		#Pull out images
		for i in range (count):
			tag = tags[i]

			images.append ({'uri' : f'../textures/{tag}.png'})

			textures.append ({'source': len (images) - 1,
							  'sampler': len (samplers) - 1})

			materials.append ({
				'name': tag,
				'pbrMetallicRoughness' : {
					'baseColorTexture': {'index': len (textures) - 1},
					'baseColorFactor': [1.0, 1.0, 1.0, 1.0],
					'metallicFactor': 0.0,
					'roughnessFactor': 1.0
				},
				'doubleSided': True,
				'alphaMode': 'MASK'
			})

	#Pull out header
	size, unk0, magick,\
	unk1, unk2, unk3, unk4, unk5, unk6,\
	nverts,\
	unk7, unk8, unk9, unk10,\
	count = unpack (HEADER_FMT, data[:HEADER_SIZE])

	tmp = data[HEADER_SIZE + calcsize ('<54I'):]
	tag = cstr_decode (tmp[:32]);
	tmp = tmp[32:]
	
	#Unknown data
	tmp = tmp[13*4:]
	
	#Song and dance for scf files
	pose = []
	joint_accessor = -1
	weight_accessor = -1
	if scf:		
		#Generate skin
		#The vertices are already stored relative to bones,
		#so we can just use the identity matrix here
		mats = bytes ()
		for i in range (len (skel)):
			mat4 = [1.0, 0.0, 0.0, 0.0,
					0.0, 1.0, 0.0, 0.0,
					0.0, 0.0, 1.0, 0.0,
					0.0, 0.0, 0.0, 1.0]
			mats += pack ('<16f', *mat4)
			
		views.append ({
			'buffer': len (buffers),
			'byteOffset': len (binfile),
			'byteLength': len (mats),
			'byteStride': 0
		})
		accessors.append ({
			'bufferView': len (views) - 1,
			'byteOffset': 0,
			'type': 'MAT4',
			'componentType': GLTF.FLOAT,
			'count': len (skel)
		})		
		skins.append ({
			'inverseBindMatrices': len (accessors) - 1,
			'joints': [x for x in range (0, len (skel))]
		})
		binfile += mats
		del mats
		
		#Generate accessors for the weights
		#For multiplexed verts this assumes the bind pose can be reconstructed
		#otherwise the result will be incorrect. Blender handles this well,
		#but your mileage may vary.
		global ssndb
		with open_file (ssndb[filename], 'rb') as f:
			binds, mplx = ssn_load (f.read ())
			
			joints = []
			weights = []
			for b in binds:
				joints += b.count*[b.bone, 0, 0, 0]
				weights += b.count*[1.0, 0.0, 0.0, 0.0]

			for m in mplx:
				joints += [m.bones[0], m.bones[1], m.bones[2], 0]
				weights += [m.bias[0], m.bias[1], m.bias[2], 0.0]
			
			#View/accessor for joints
			joint_bin = pack (f'{len (joints)}I', *joints)
			joint_accessor = len (accessors)
			views.append ({
				'buffer': len (buffers),
				'byteOffset': len (binfile),
				'byteLength': len (joint_bin),
				'byteStride': 0
			})
			accessors.append ({
				'bufferView': len (views) - 1,
				'byteOffset': 0,
				'type': 'VEC4',
				'componentType': GLTF.UNSIGNED_INT,
				'count': len (joints)//4
			})
			binfile += joint_bin
			del joints
			del joint_bin

			#View/accessor for weights
			weight_bin = pack (f'{len (weights)}f', *weights)
			weight_accessor = len (accessors)
			views.append ({
				'buffer': len (buffers),
				'byteOffset': len (binfile),
				'byteLength': len (weight_bin),
				'byteStride': 0
			})
			accessors.append ({
				'bufferView': len (views) - 1,
				'byteOffset': 0,
				'type': 'VEC4',
				'componentType': GLTF.FLOAT,
				'count': len (weights)//4
			})
			binfile += weight_bin
			del weights
			del weight_bin
	
	#Repack position data and create a view/accessor for it
	vtx = bytes ()
	for i in range (nverts):
		x, y, z, w = unpack ('<4f', tmp[:4*4])
		vtx += pack ('<3f', x, y, z)
		tmp = tmp[4*4:]
	
	position_accessor = len (accessors)
	views.append ({
		'buffer': len (buffers),
		'byteOffset': len (binfile),
		'byteLength': len (vtx),
		'byteStride': 0
	})
	accessors.append ({
		'bufferView': len (views) - 1,
		'byteOffset': 0,
		'type': 'VEC3',
		'componentType': GLTF.FLOAT,
		'count': nverts
	})
	binfile += vtx
	del vtx
		
	#Repack normal data and create a view/accessor for it
	nml = bytes ()
	for i in range (nverts):
		x, y, z, w = unpack ('<4f', tmp[:4*4])	
		nml += pack ('<3f', x, y, z)
		tmp = tmp[4*4:]
	
	normal_accessor = len (accessors)
	views.append ({
		'buffer': len (buffers),
		'byteOffset': len (binfile),
		'byteLength': len (nml),
		'byteStride': 0
	})
	accessors.append ({
		'bufferView': len (views) - 1,
		'byteOffset': 0,
		'type': 'VEC3',
		'componentType': GLTF.FLOAT,
		'count': nverts
	})
	binfile += nml
	del nml
	
	#Unknown data
	tmp = tmp[nverts*4*3:]
	tmp = tmp[count*4:]
	
	strips = []
	ndx = bytes ()
	txc = bytes ()
	for i in range (count):
		STRIP_FMT = '<IHHI'
		STRIP_SIZE = calcsize (STRIP_FMT)
		unk0, slot, flags1, nelem = unpack (STRIP_FMT, tmp[:STRIP_SIZE])
		tmp = tmp[STRIP_SIZE:]
		
		nndx = len (ndx)
		ntxc = len (txc)
		
		#Indices and UVs are written out in 8 element granularities
		aligned = (nelem + 7)&~7;
			
		#Repack index data into the blob
		indices = list (unpack (f'<{aligned}I', tmp[:4*aligned]))
		ndx += pack (f'<{nelem}I', *indices[:nelem])
		tmp = tmp[4*aligned:]
		
		#Append the UVs to the blob	
		uvs = list (unpack (f'<{2*aligned}f', tmp[:4*2*aligned]))
		txc += pack (f'<{2*nelem}f', *uvs[:2*nelem])
		tmp = tmp[4*2*aligned:]
		
		strips.append (Strip (nelem, slot, nndx, ntxc))
		
	index_view = len (views)
	views.append ({
		'buffer': len (buffers),
		'byteOffset': len (binfile),
		'byteLength': len (ndx),
		'byteStride': 0
	})
	binfile += ndx
	del ndx
	
	texco_view = len (views)
	views.append ({
		'buffer': len (buffers),
		'byteOffset': len (binfile),
		'byteLength': len (txc),
		'byteStride': 0
	})
	binfile += txc
	del txc
	
	#Format the strips out as primitives
	prims = []
	for strip in strips:
		accessors.append ({	
			'type': 'VEC2',
			'componentType': GLTF.FLOAT,
			'count': nverts,
			'sparse': {
				'count': strip.length,
				'values': {
					'bufferView': texco_view,
					'byteOffset': strip.uv_offset,
				},
				'indices': {
					'bufferView': index_view,
					'byteOffset': strip.index_offset,
					'type': 'SCALAR',
					'componentType': GLTF.UNSIGNED_INT,
				}
			}
		})
		accessors.append ({
			'bufferView': index_view,
			'byteOffset': strip.index_offset,
			'type': 'SCALAR',
			'componentType': GLTF.UNSIGNED_INT,
			'count': strip.length
		})
		attributes = {
			'POSITION': position_accessor,
			'NORMAL': normal_accessor,
			'TEXCOORD_0': len (accessors) - 2,
		}
		
		if scf:
			attributes['WEIGHTS_0'] = weight_accessor
			attributes['JOINTS_0'] = joint_accessor
			
		prims.append ({
			'mode': GLTF.TRIANGLE_STRIP,
			'indices': len (accessors) - 1,
			'material': strip.slot,
			'attributes': attributes
		})
	
	#Pull all the strips together under one mesh
	meshes = [{
		'name': tag,
		'primitives': prims
	}]
	
	#Generate a mesh node to tie it all together
	if scf:
		nodes.append ({
			'name': tag,
			'mesh': len (meshes) - 1,
			'skin': len (skins) - 1,
		})
	else:
		nodes.append ({
			'name': tag,
			'mesh': len (meshes) - 1,
		})
	
	#Generate buffer bits
	buffers.append ({
		'byteLength': len (binfile),
		'uri': f'{filename}.bin'
	})
	
	#Store the blob to disk
	with open (os.path.join (prefix, filename + '.bin'), 'wb') as f:
		f.write (binfile)
	
	#Fix up coordinate space
	#rotates 90 degrees on y, and -90 on z
	#this might be blender specific 
	nodes[0]['rotation'] = [0.5, 0.5, -0.5, 0.5]
	
	return {
		'nodes': nodes,
		'buffers': buffers,
		'materials': materials,
		'textures': textures,
		'samplers': samplers,
		'images': images,
		'meshes': meshes,
		'skins': skins,
		'accessors': accessors,
		'bufferViews': views,
	}

def saf_decode (data, prefix, filename, skel, animset):	
	HAS_EVENTS = 0x02
	HAS_POSITIONS = 0x10
	HEADER_FMT = '<32s4Bf2I'
	HEADER_SIZE = calcsize (HEADER_FMT)

	tag,\
	flags, unk0, unk1, unk2,\
	fps, version, count = unpack (HEADER_FMT, data[:HEADER_SIZE])
	tmp = data[HEADER_SIZE:]
	
	verify (1 == version, f'Version is not 1!')
	
	count += 2
	offsets = list (unpack (f'{count}I', tmp[:4*count]))
	tmp = tmp[4*count:]
	
	#Do some simple verification
	#This catches the corrupted 'e05_boneskel_throw_11' animation
	#in a general sort of way
	size = len (data)
	for pos in offsets:
		verify (pos < size, f'Offset "{pos}" outside of data ({size})')
	
	#This is kind of a hack, but logically sound
	nbones = (offsets[1] - offsets[0])//4//4 - 1
	if len (skel) != nbones:
		print (f'        animation references {nbones} bones; skeleton only has {len (skel)}!')
		return {}
	
	times = []
	rotations = []
	positions = None
	basepos = []
	for i in range (count):
		times.append (unpack ('<I', tmp[:4])[0])
		tmp = tmp[4:]
		
		#Rotations for each bone 
		data = []
		for j in range (nbones):
			data.append (unpack ('<4f', tmp[:4*4]))
			tmp = tmp[4*4:]
		
		#Position for root joint
		basepos.append ([unpack ('<4f', tmp[:4*4])])
		tmp = tmp[4*4:]
		
		rotations.append (data)
	
	#Skip the event data
	if flags&HAS_EVENTS:
		nevents, unk3 = unpack ('<2I', tmp[:4*2])
		tmp = tmp[4*2 + 36*nevents:]
		
	#Position keys
	if flags&HAS_POSITIONS:
		positions = []
		for i in range (count):
			data = []
			for j in range (nbones):
				data.append (unpack ('<4f', tmp[:4*4]))
				tmp = tmp[4*4:]
			
			positions.append (data)
	
	animations = animset['animations'] if 'animations' in animset else []
	views = animset['bufferViews'] if 'bufferViews' in animset else []
	accessors = animset['accessors'] if 'accessors' in animset else []
	buffers = animset['buffers'] if 'buffers' in animset else []
	samplers = []
	channels = []
	
	tbin = bytes ()
	rbin = bytes ()
	pbin = bytes ()
	for i in range (1, count - 1):
		tbin += pack ('<f', times[i]/fps)
		
		for k in rotations[i]:
			rbin += pack ('<4f', *k)
		
		if None is not positions:
			for k in positions[i]:
				pbin += pack ('<4f', *k)
		else:
			for k in basepos[i]:
				pbin += pack ('<4f', *k)			
	
	binfile = tbin + rbin + pbin
	
	with open (os.path.join (prefix, filename + '.bin'), 'wb') as f:
		f.write (binfile)
		
	#Append buffer
	buffer_index = len (buffers)
	buffers.append ({
		'byteLength': len (binfile),
		'uri': f'{filename}.bin'
	})
	
	#Create the views for each bank
	view_index = len (views)
	frame_size = 16*nbones
	views.append ({
		'buffer': buffer_index,
		'byteOffset': 0,
		'byteLength': len (tbin),
		'byteStride': 0
	})
	views.append ({
		'buffer': buffer_index,
		'byteOffset': len (tbin),
		'byteLength': len (rbin),
		'byteStride': frame_size
	})
	if None is not positions:
		views.append ({
			'buffer': buffer_index,
			'byteOffset': len (tbin) + len (rbin),
			'byteLength': len (pbin),
			'byteStride': frame_size
		})
	else:
		views.append ({
			'buffer': buffer_index,
			'byteOffset': len (tbin) + len (rbin),
			'byteLength': len (pbin),
			'byteStride': 0
		})		
		
	#Create accessor for time keys
	time_keys = len (accessors)
	accessors.append ({
		'bufferView': view_index,
		'byteOffset': 0,
		'type': 'SCALAR',
		'componentType': GLTF.FLOAT,
		'count': count - 2
	})
	
	#Create bone data
	for i in range (nbones):
		accessors.append ({
			'bufferView': view_index + 1,
			'byteOffset': i*16,
			'type': 'VEC4',
			'componentType': GLTF.FLOAT,
			'count': count - 2		
		})
		samplers.append ({
			'input': time_keys,
			'interpolation': 'LINEAR',
			'output': len (accessors) - 1
		})
		channels.append ({
			'target': {
				'node': i,
				'path': 'rotation'
			},
			'sampler': len (samplers) - 1
		})
		if None is not positions:
			accessors.append ({
				'bufferView': view_index + 2,
				'byteOffset': i*16,
				'type': 'VEC4',
				'componentType': GLTF.FLOAT,
				'count': count - 2		
			})
			samplers.append ({
				'input': time_keys,
				'interpolation': 'LINEAR',
				'output': len (accessors) - 1
			})
			channels.append ({
				'target': {
					'node': i,
					'path': 'translation'
				},
				'sampler': len (samplers) - 1
			})
	
	#Just emit base positions
	if None is positions:
		accessors.append ({
			'bufferView': view_index + 2,
			'byteOffset': 0,
			'type': 'VEC4',
			'componentType': GLTF.FLOAT,
			'count': count - 2		
		})
		samplers.append ({
			'input': time_keys,
			'interpolation': 'LINEAR',
			'output': len (accessors) - 1
		})
		channels.append ({
			'target': {
				'node': 0,
				'path': 'translation'
			},
			'sampler': len (samplers) - 1
		})
			
	animations.append ({
		'channels': channels,
		'samplers': samplers,
		'name': filename
	})
		
	return {
		'animations': animations,
		'buffers': buffers,
		'bufferViews': views,
		'accessors': accessors
	}

class Lex:
	def __init__ (self, data):
		self.lines = [x for x in data.decode ().splitlines () if x != '']
	
	def next (self):
		if len (self.lines) == 0:
			return ''

		ln = self.lines[0].strip ()
		self.lines = self.lines[1:]
		return ln
	
def readbin (path, args):
	global sskdb, ssndb, scfdb
	
	#.bin writes everything out in 2k pages
	ALIGNMENT = 2048
	
	textures = []
	models = []
	
	animset_script = ''
	animsets = {}
	def animset_parse (data):
		lx = Lex (data)
		#Ensure that this is an animation set
		ln = lx.next ()
		if 'ANIMSET_DEF_FILE' not in ln:
			raise Exception (f'Expected "ANIMSET_DEF_FILE"; got "{ln}"')
		#Parse the contents of the script
		ln = lx.next ()
		while 'ENDFILE' != ln:
			if 'ANIMSET_DEF' == ln:
				tag = lx.next ().lower ()
				num = int (lx.next ())

				#Parse out each animation
				anims = []
				for i in range (num):
					ln = lx.next ()
					if '' == ln:
						raise Exception (f'Unexpected EOF in "{fn}"')
					anims.append (ln.lower ())

				#Store the animset for later
				animsets[tag] = anims

				#Proceed to next line
				ln = lx.next ()
				continue

			#Unknown syntax
			raise Exception (f'Unexpected token, "{ln}"')	
	
	actor_script = ''
	actors = {}
	def actor_parse (data):
		lx = Lex (data)
		#Ensure that this is an animation set
		ln = lx.next ()
		if 'ACTOR_DEF_FILE' not in ln:
			raise Exception (f'Expected "ACTOR_DEF_FILE"; got "{ln}"')
		#Parse the contents of the script
		ln = lx.next ()
		while 'ENDFILE' != ln:
			if 'ACTOR_DEF' == ln:
				tag = lx.next ()
				base = lx.next ().lower ()
				anims = lx.next ().lower ()
				unk0 = int (lx.next ())
				unk1 = int (lx.next ())
				unk2 = int (lx.next ())
				unk3 = int (lx.next ())

				#Insert the actor into the DB
				actors[tag] = (base, anims)

				#Proceed to next line
				ln = lx.next ()
				continue

			#Unknown syntax
			raise Exception (f'Unexpected token, "{ln}"')
	
	#Create prefix directory where to store everything
	mkdir (args.prefix)
	
	#Try to open a stream to the blob
	try:
		with open (path, 'rb') as f:
			file_length = os.fstat (f.fileno ()).st_size
			print (f'Reading "{path}"...')
			print (f'Length: {file_length}')

			#There seems to be no directory count, so in order to read
			#all the directories we have to go until the end of file
			while f.tell () < file_length:
				unk0, unk1 = unpack ('<II', f.read (8))
				dn = cstr_decode (f.read (32))
				print (f'Reading directory "{dn}"...')

				nfiles = unpack ('<I', f.read (4))[0]
				print (f'  Files: {nfiles}')
				
				#Grab bin name
				bfn = os.path.splitext (os.path.split (path)[1])[0].lower ()
				mkdir (os.path.join (args.prefix, bfn))
				mkdir (os.path.join (args.prefix, bfn, dn))

				for i in range (nfiles):
					#Read the file header...
					fn = cstr_decode (f.read (32))
					sz, unk3 = unpack ('<II', f.read (8))
					fp = f.tell ()
					print (f'  Reading file "{fn}" ({sz}) ({unk3}) @ {fp} ({i})...')

					#Now for the file contents...
					op = os.path.join (args.prefix, bfn, dn, fn)
					data = f.read (sz)

					#Actor definitions refer to assets via a symbolic name,
					#so we have to map the files to their symbolic names,
					#which means... we have to peek them.... sigh...
					#NB: a symbolic name is NOT necessarily the same as the file name
					dnl = dn.lower ()
					if 'ssk' == dnl:
						sskdb[ssk_symbolic_name (data)] = os.path.join (dn, fn)
					elif 'ssn' == dnl:
						ssndb[ssn_symbolic_name (data)] = os.path.join (dn, fn)
					elif 'scf' == dnl:
						tag = smf_symbolic_name (data)
						scfdb[tag] = os.path.join (dn, fn)
						smtdb[tag] = fn
					elif 'smf' == dnl:
						if args.models:
							models.append (fn)
					elif 'textures' == dnl:
						if args.textures:
							textures.append (op)
					else:
						#Parse out animation sets and actor scripts
						#This because there is no other linkage between model and animation,
						#and the animations need a skeleton to display properly
						if '_animsets.txt' in fn:
							animset_script = data

						if '_actors.txt' in fn:
							actor_script = data
					
					#Write the raw binary data
					with open (op, 'wb') as cont:
						cont.write (data)

					#Skip to the next page boundary
					f.seek ((f.tell () + (ALIGNMENT - 1))&~(ALIGNMENT - 1))
				
	except FileNotFoundError:
		print (f'Could not open stream to "{path}"; ignoring...')
		return
	
	#No further processing requested
	if args.raw:
		return
	
	#Convert textures to png
	if args.textures:
		print ('Converting textures...')
		for txt in textures:
			with open (txt, 'rb') as f:		
				ret, mode = pvr_decode (f.read ())
				verify (str != type (ret), f'image "{txt}" failed to decode: {ret}!')
				png.from_array (ret, mode).save (txt + '.png')

	#Convert models to glTF
	if args.models:
		print ('Converting models...')
		for mdl in models:
			try:
				print (f'  {mdl}')
				with open_file (os.path.join ('smf', mdl), 'rb') as f:
					gltf = {
						'asset': {
							'generator': "Castlevania: Resurrection Tools",
							'version': '2.0'
						}
					}

					gltf.update (smf_decode (f.read (), os.path.join (args.prefix, bfn, 'smf'), mdl, None, gltf, mdl))
					
					gltf['scenes'] = [
						{'nodes': [x for x in range (len (gltf['nodes']))]}
					]

					with open (os.path.join (args.prefix, bfn, 'smf', f'{mdl}.gltf'), 'w') as f:
						f.write (json.dumps (gltf))

			except Exception as e:
				print (f'ERROR: {e}')	
		
	#Pull together actors
	if args.actors:
		#Store all these files in their own directory
		actor_path = os.path.join (args.prefix, bfn, 'actors')
		mkdir (actor_path)
		#Parse the data out from the scripts
		animset_parse (animset_script)
		actor_parse (actor_script)
		#Assemble all the data sets
		for k, v in actors.items ():
			if v[1] not in animsets:
				continue

			print (f'Building glTF file for "{k}"...')
			print (f'  Processing skeleton...')
			skel = []
			with open_file (sskdb[v[0]],'rb') as f:
				bones = ssk_load (f.read ())
				for i in range (len (bones)):
					b = bones[i]
					skel.append ({
						'name': b.tag,
						'children': b.children,
						'translation': [b.rows[0][0], b.rows[0][1], b.rows[0][2]]
					})

			print (f'  Processing animations...')
			animset = animsets[v[1]]
			num = 1
			for anim in animset:
				try:
					gltf = {
						'asset': {
							'generator': "Castlevania: Resurrection Tools",
							'version': '2.0'
						}
					}
					gltf.update ({'nodes': skel[:]})

					print (f'      {num:2}/{len (animset):2} "{anim}"...')
					with open_file (os.path.join ('saf', anim), 'rb') as f:
						obj = saf_decode (f.read (), os.path.join (args.prefix, bfn, 'actors'), anim, skel, gltf)
						gltf.update (obj)

					num += 1

					mdl = {}
					with open_file (scfdb[v[0]], 'rb') as f:
						matname = smtdb[v[0]]
						mdl = smf_decode (f.read (), os.path.join (args.prefix, bfn, 'actors'), v[0], skel, gltf, matname)
						gltf.update (mdl)

					gltf['scene'] = 0
					gltf['scenes'] = [{'nodes': [x for x in range (len (gltf['nodes']))]}]

					with open (os.path.join (actor_path, f'{k}_{anim}.gltf'), 'w') as f:
						f.write (json.dumps (gltf))

				except Exception as e:
					print (f'ERROR: {e}')
		
def main ():
	p = argparse.ArgumentParser (description = 'Extracts Castlevania: Resurrection .bin file contents')
	p.add_argument ('files', metavar='file', type=str, nargs='+', help='A .bin file to extract')
	p.add_argument ('--prefix', type=str, default='contents', help='Path to store the assets')
	p.add_argument ('--raw', default=False, action=argparse.BooleanOptionalAction, help='Toggles asset processing')
	p.add_argument ('--textures', default=True, action=argparse.BooleanOptionalAction, help='Toggles texture conversion')
	p.add_argument ('--models', default=True, action=argparse.BooleanOptionalAction, help='Toggles model conversion')
	p.add_argument ('--actors', default=True, action=argparse.BooleanOptionalAction, help='Toggles actor conversion')
	
	args = p.parse_args ()
	
	global fspaths
	for a in args.files:
		bn = os.path.splitext (os.path.split (a)[1])[0].lower ()
		fspaths.append (os.path.join (args.prefix, bn))
	
	for a in args.files:
		readbin (a, args)

if __name__ == "__main__":
	main ()
