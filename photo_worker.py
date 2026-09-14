import sys,fitz
pdf=sys.argv[1]; page_no=int(sys.argv[2]); x0=float(sys.argv[3]); y0=float(sys.argv[4])
d=fitz.open(pdf); p=d[page_no-1]
# Original photo rectangle is 135px right of card marker and 44.15px high.
r=fitz.Rect(x0+135,y0+22.3,x0+172.5,y0+66.45)
pix=p.get_pixmap(matrix=fitz.Matrix(3,3),clip=r,alpha=False)
sys.stdout.buffer.write(pix.tobytes('jpeg'))
