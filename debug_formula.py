"""Debug: find correct extended-coordinate formula for twisted Edwards a=-1."""
from prism.crypto.field import P, D, modp, inv

By = 4*inv(5)%P
yy = modp(By*By); u = modp(yy-1); v = modp(D*yy+1)
x2 = u*inv(v)%P
r = pow(x2,(P+3)//8,P)
if (r*r-x2)%P: r = r*pow(2,(P-1)//4,P)%P
Bx = r if r%2==0 else P-r
B=(Bx,By)

def affine_add(p,q):
    (x1,y1),(x2_,y2)=p,q
    k=modp(D*x1*x2_%P*y1*y2)
    xx=modp(modp(x1*y2+y1*x2_)*inv(modp(1+k)))
    yv=modp(modp(y1*y2+x1*x2_)*inv(modp(1-k)))
    return (xx,yv)

# Test the STANDARD HWCD extended add formula from the paper
def ext_add(Pt,Qt):
    xh1,yh1,zh1,th1=Pt; xh2,yh2,zh2,th2=Qt
    a=modp((yh1-xh1)*(yh2+xh2)); b=modp((yh1+xh1)*(yh2-xh2))
    c=modp(2*D*th1*th2); dd=modp(2*zh1*zh2)
    e,f,g,h=b-a,dd-c,dd+c,b+a
    # Standard order: X3=E*F, Y3=G*H, Z3=F*G, T3=E*H
    return (modp(e*f),modp(g*h),modp(f*g),modp(e*h))

def to_affine(ext):
    xh,yh,zh,th=ext
    zi=inv(zh)
    return (modp(xh*zi),modp(yh*zi))

# Test with B + [2]B = [3]B
B_ext=(Bx,By,1,modp(Bx*By))
B2=affine_add(B,B)
B3=affine_add(B2,B)
B2_ext=(B2[0],B2[1],1,modp(B2[0]*B2[1]))
s=ext_add(B_ext,B2_ext)
got=to_affine(s)
print('Standard ext_add(B, B2) == B3:', got==B3)
print('  got:', got)
print('  ref:', B3)

# Try different sign conventions for E,F,G,H
variants = {
    'E=B-A,F=D-C,G=D+C,H=B+A': lambda a,b,c,d: (b-a, d-c, d+c, b+a),
    'E=A-B,F=C-D,G=D+C,H=B+A': lambda a,b,c,d: (a-b, c-d, d+c, b+a),
    'E=B-A,F=C-D,G=D+C,H=B+A': lambda a,b,c,d: (b-a, c-d, d+c, b+a),
}
for name, fn in variants.items():
    xh1,yh1,zh1,th1=B_ext; xh2,yh2,zh2,th2=B2_ext
    aa=modp((yh1-xh1)*(yh2+xh2)); bb=modp((yh1+xh1)*(yh2-xh2))
    cc=modp(2*D*th1*th2); dd=modp(2*zh1*zh2)
    e,f,g,h=fn(aa,bb,cc,dd)
    o=(modp(e*f),modp(g*h),modp(f*g),modp(e*h))
    ox,oy=to_affine(o)
    print(f'{name}: match={((ox,oy)==B3)}')
