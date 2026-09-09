# -*- coding: utf-8 -*-
"""Compiled game data for raw_craft, embedded in this module.

The payload is zlib+base85-compressed JSON embedded as a literal.
Decompressed once at import time — `from . import raw_X; raw_X.DATA` keeps
working everywhere (dev + frozen).
"""
import base64
import json
import zlib

_PAYLOAD = (
    b"c-rM%+j84Fvi%j6p0g8)vXqS9Y{!l>6Yr=j@3?B8N~s_Tk}xI-4M^HicBSh4#`(SZl4<ZJNW_(-Z"
    b"*!g!i)3T1MmM?}0Kvby(`kTXG@W*j|LV?ZupvHX-Q)7VZ#UcSo9+U^E%%s3gobd@>v^N!`n}(J{T"
    b"pv^JRBYm-@o;GN2B+>KfAx)bpJ_bQTO<N{_3ulKe*T>2!f~n`223{qd8tve}UP%&vP0^(`y{jgw1"
    b"jEb`!3kr8Oq2b=*Dn-gG1K6COMI@TU7f7V$d$4de|*Z@OFf=L57v79jVEgd}~I;W^phE4siwZ~yz"
    b"7Zj67#5r?qA(Hw65gyJ=39m=3RcpyXp=2tg<!jPYd0+Mj$zWAg23z~^3^>4NtPT~_rY)s~N=m&<|"
    b"lFf=Ckj-2B_2#JvUt%^xK8XSs&Kn<vv7B`wFD{suKgifmfW7~G<S$6HcI@sYJfi4uE)BB)hdJ~7Z"
    b"xp`7em`OJHKsmYZC%(S7Lz5KEBd~aDRjU1TT?wy7BfWR=-uZS2hqD5kaP;Vxr+i4ud^SgUnq^I{7"
    b"N*vL_vVrwAX*TVR(sVxw0}1dBahvm|m)vUZIwqysBFM>IVG3a97qoZ|dE3dQbk(boY4PCYOR3iJ^"
    b"_|84Us$@;J9<g8m5SZHc-@47VWWd_(3fi1~t7I9wpM?f#P3;2)unQMB%WC3coR7+=((%g%Vq;3wL"
    b"d!IK>xetiDd7W||0KzzT0(x3gf!_WgCtUDxx>x|BlDDEKNr`zal=Yp~o?l9~VH1qj?NVufnVBpMg"
    b"l!{nmbiW1r4=4iwDDKQr2o1#RDCU<rrhtOPCBbalnV~rL6~n|%1BH(WM)#|*x{n#>8;rnzBF?9?r"
    b"VpIjoS|jhIX)(!;^X7DrJeqsHq?dDB0MfHclSMR-(~7L-H8vF0&wW6&t1Xo$kFFYakX5oe7d2>>E"
    b"yUMoKBLxzv-mdyPHmm-rFt_3vm>smc1s61r9qK`oI&OWgh=izra5>KKCkqTZHEf$EjEHKf$SRKzZ"
    b"B%5BmaReaOuhnqd$@(mNIyZ!kE`hQ{D%1Y8#$4N=TJ1^jfy@Pd>cIHTOBIwa0K_#SRgo7yDMJlu+"
    b"6j#nO-3N4gTsWiFf4wq?K>vE!n*YdcpNxUR+%oBh^kDJU%6j2tr`CKWmmd|BR^Mt;U%W1^8d7MU&"
    b"y~AmQ*!!DCh~C}KeKeyC#hE8<Fk5195>Ud7fI~p3#7#Yb>nyc<Fv}Pb@Vw3nv0ao}0-^`-Gzdcwi"
    b"qcS(`v!M2@I<NzN~Jv%9<no#7D5u@g7i^IG{{;m@^}1zeP8AwW1sLL$lf^$QMG)dc(|c&XvDbrhD"
    b"MORZ)k+r`-Vn{-Z!rCJ?3d0j~L`0Drv~&76gTJED1IL{@?%0GBFYaXrB1orRHo4X_^mVXiT9LO;g"
    b"Aq^`SJlt=8N-78o2Ql?d>l5$;u4hzsW^O8t^Bh&^x(tT+#bX(*zb#OF6N2w9mQF3``PKK#`QF%-C"
    b"HuJF24BRikV|9pxhP%!aY-gZXmUDeb65t1c#a5OHZmXq-Z8#<Umgqw3IgxEWlLV&$fDFm2;4Zbfz"
    b"4HPyk%h?dGmKOJ5$&gX8D6MYNx(A2;7V@omoTNUK&jmnj4VBKE(T4>XJ);TOeOh+;=$`x75{k7rd"
    b"ed1ZIP={ZU2J(fSs2RV6@lC>+2p<jlN!F%PdLfKNm846lB_Ma&RYI)9?sX0Bg?|kEf9B0)=FWue5"
    b"HK4p~L9pxOt6ElD+Hbq}cn9PKv>aghD1jD>8==1qBGs!%`X_mWkh4@qCOMJA{rT+7&P;1aFxA6Vk"
    b"l5owGF0%N!CaDh$(XQ7R?kvk<8_atVC;Eka?M{;^$9EF#!m8O1%Kga4I~4G&~ew<@_8zO^&7<_-o"
    b"SIe_S^^)S(}_uTAzAuO};ImGks@%th8L%2!e{I?{8|G`8j7oXbl1{C)>T+uk~`uv9eu)O1fgwsF!"
    b"dlz@t7+P^feO=z7Q~}@1OScFrx9Rm23i;&k^n!4Qg>{L;=|UJ(xvhcB$fw*?dRa?K?ERhzytljK0"
    b");CJ^ygqBYZ3h!H;w7_Kj;i*kU991D00W&@AYH^JQIOIdBcP*<BNnT*xix}J5R#ycRn|OMS7<|V0"
    b"wLuF{gG*eooK0S!xM-4g`lTJ>+6uvYW;*QurddOK|#V&+?%2YBTio{&UJ`_!DdWr)q+`i<yC4@d;"
    b"Q{FIH^y0^>asJ@c^z%faKYl$he~;wPU*(e`XjViLL=Yd(_fthENep}`Rq1a}m!QLq9%SM`6!ND$F"
    b"5?*J{dp#wCd?my_D<-?bN&B8LI@Vs6<c>Pg%$44kKLo&al%-H#7Yeb_xU&d%Ly-p=qvKZ~Ns6am)"
    b"o4)3mQ)OJ4Y!JJfpkU+gS)(|j83KY2i4zwTK#g!V?su(`-6T*FLso)a!Icl1@p&eELN{rUl2D0Jm"
    b"0Flch!$n=bVm*MO;#%e-k63kG0CtpI34I(AaKIh$M|zP^hYK7Qy4kM+3lA0aZMq7t8kf-EA)Q5hG"
    b"hu;u%%tU7~JVPehbL65l=cE52~FjAh<48J{OtIkNNzWsrOrH`4ULiX%s4l(1+fbq&p(Y%6?jr*Gw"
    b"--yXY$N8ljuOTqtbJAgw}_3vGn+3Z`MnyhV&vfsOEef`T4H&E6tHnaD;MKc+L_ZFaOoqM>lz<xDp"
    b"yghyd<mrn_{TXttjX;J$?;Zv>d#6=_L>U&4x3(yZwsqawtSe4#+`Igr;)uP50zOndL_JXG?zx&$B"
    b"1*^g>FKBXx&_`Azy;>xty`(L_y&AJSRz~JfUkhfW_7%@qUk<hNC|1R1UP<xd)7F_BDr|8o2p(A<b"
    b"j85Z5S!K4*^t2*0zE5Jbg8i|dh$p|^GH|cX7gA@nQQAw4t2QTDY6b4>5#ir=(d`#SEJX{ykKD8Fs"
    b"$1w_#BUO1wUEC`V?%!jfyh9IvFS7IjY4|)<t@XBSA;RpZH{s8_iLq#PZ&B;HSxEqyzXs)wSObo<l"
    b"1`+^k*8NpV>?yJSog{TTwPXBc`MAq%R^(74u@`&6vdX*(e?(3-9h*{%;-MWxSwEF!baNCbZ&3q0c"
    b"s-?w~~TnGKAm5TkKJ!##39?Tep``Ffir^VLOTHCo{ZRIXVYePLrn*a}`wE%EeY`2C|DzXKrS<T%V"
    b"%!;@cAQnY;Yak^8TY#At-mRgm$}=rM8>cKaRiLy~zCsH|c!oI676QNTxA1j2=G!7lp5n%6_vQ0i="
    b"!>dB3jYh=?6iPw3o7_$NtnMN_-q8K3&;-Yza`Ov2G0q<YCS!B^<VS(aZHnlZ}oR(A)jJ!S**%e=&"
    b"j~~^fyV&&}Oz`yoOER5#2zZGTB=ukm$f=mO`lJ2uuSmZj$Iuo(`7{oo9QUluPAhsVG$>gI7*rU;D"
    b"h^Dw6!1Zj{O}X!tqQ?!xrRhQRKwOTOum=jYEwah$L;G^apxB##^>DAZ>9wA_k|84P=JnjorQZ<E&"
    b")SFjzf@J9VmQM=YTg(nyFaODHQBLUORxocr%BTlQRJY{&2MT)~1AiL)zob&g>Zr0lXS8g*By?{6N"
    b"*B3rdL?9bk=;=0Dny)q_h50A!<D_vC>6pM)({P0+8-Cir{sMi~ovqlo&pD{3W-SftgtV+@Va9<vT"
    b"0n;Ag6RFPS=SdKJZ<IaG6t?@*Byx3y<yd%zKMNlDkh=V2=A~n1hcJ-Ds1iCCqD@Lx&+U_;fhZ<dH"
    b"Nea!uD;<H+CEE{0uvieV?o{e=^*B0UQ6-jZ34gkkxX#D(@)?YZN@Rw!m*h<rL~sxNk8ETL(YYkOf"
    b"DfPzHGqe?MwW`Kyg~;l_5vQmbHKp@M<ks;`n=VHYXO!Z=?hLCHo}SAwNeJv~Dk3)N;pRs_Yd;AL_"
    b"=-{OWG(;@7s$hDOW+Li`-4bV&jC;V6sLn*D+fNDBU(!?_<%QGw-dDc6LO$K!`gNaULaFzzz6kB+N"
    b"0O+~w;nW7W$(mqjz!=|Z>7v@gBMdR4X2a+g8m><H$ph<0QbMJH+V)^sr`e_AN`-t5;k8&YBR+&nv"
    b"r3NYs6^7l<0xfdw{y%mT!`6wU1dBDL(gGLl9A?K?eS81{*E^hSxn$HR$P<jVd3;rnX2c29mtl2-^"
    b"*g@G$)`BVc#%}F^@M`$=Y~qohWR)Va0F(2OUfvNbpFCIuQSKc2k*_a&V6{-!-_$uu&dZH1Lf2u&@"
    b"m=OM47;9Lg8=80r`LTGUIvkFZ9PoQHq2<H&bWzI=vd<p1xbm<GcCUV36u<IJ*sZbQ?{$TnjVn)B6"
    b"XOFlKmqyzbntqJ*_ZVN?x<L9;bkEDe?;fAd=hZ8Up9LE|u@~H{oy|G|+@GcbyOJ{wqvu)k5S-<2f"
    b"90lgQ1*m1*r?6+u*Wtbp7HeM+Zlk_WHm_3MSI)5ITda=ExLw{?ubh+edw$AZiQE~sS5w^s7b{t%u"
    b"U1yo?d`nsWUPTccsA(Q$!x1b4t{y%j(k|tRxCFu<z6Yflv}>+QbtuOZ?$_hwlbp(<_9xGSZ<Y`(J"
    b"EbD<Z7d9#kX@|{ukbSgmonQ^J4PuA6SKx(W~aB4>_-!n>H-HZfaLf)Kd99n<Lul9kMfKG}^#Pf8y"
    b"40X=uvROxaWJh?^h2<BD7HU=8S+(|A;%NoWUmw@In;h`x4{+QN5*?c1o!eXCoYeXwUqyuVkx3dw7"
    b"*)vXd{2i2DM(PU59pmCozuS9kun(T?kK_lb^4v2P7*%+}qs!Aeb?y(ipqG__wahPx!!K4o~z+$xr"
    b"u{6`Ymr!7L-pbB^TWOGg=P85(Qr((VzP5NMt(h`l8wQ+AyJ!bnMOs5_Y(-pqN)2sam<zH>g?)pAP"
    b"0rI|+%Z9`NNa@cB#zTlsak<{D6A2(PXx|gXbISXEGIiVEZ}zof_Z$bH6R=|D7vtHlQsjw%Hm)ugE"
    b"9mcZjP-Yts%D3hueU*FU$qm;Y5ny!RD~$FdPU$wH~mY|2D^UAgc+i(tDeu+81U0I=7-Y?c1EM@{t"
    b"Dl*L=<r7Nw1IF;<1D@}+ON%qQV<$`aT#2&<dz(UFUM5;osp#i~6b6=_ex0k-3TEkr?kJfsp0C+g_"
    b"m4AH+>A+KRC6aJZi2}*>W#rgL+S~#Uxj~6DCW>>J!)D?62{nvJ3yS?P0-rsA)Ke?mundXlyeNCCo"
    b"A_W7B<Cml2l!8MpBYPzuXmfAgh2BjEYW+H6ncOAeofeE}j)zD_jghpq?Z2<?%0ti})Sc_zm1~S6&"
    b"AxdPmZKK2oKnPU_hjD;L^W#W&s*cXrT(%uPOl+O&mJc~n`v9WYu`6S`raPt6&$Dt$*^B*G#NI;IT"
    b"UaXdU|i6TX-9p_q}_^|4ji?y4-5OF?oQ)Gq0)ggnLuh_Ir)Nh-*Z!LzlnYu~5BrP(xdNl|LWVKnO"
    b"?7BgH|6<0RPJDc_Md>d|YlQ?Kb3!MFZ=e_I#Lz^pU<7&vfng*<S0HTw3bDb6E%oMUo_YYi*?VN;Y"
    b"Y0}EtS=c`pcprfWhM<$@+>;66%;SEDu)z>%Dp-z1bh91quzV4#p?T<ZElb&a#)ZYdF?4d$?Lu(iA"
    b"+gF1|AI{EmRSnOh8pDcl>#5FFGdO)S9Ib&BQmhAT(<V`aM`Z1Jt)TYoomhI7uXEoIT7f;VSGK}H2"
    b"DZG+_H)z<rK6{#WMyB&R#*+&^9oA?8?}OaWSUok1q-rBaO64@lYeJ2+e0oMktBYAvi|v2yL};J{)"
    b"NuDUnzcj%6AXN$y9#0;Ws$B^DCR_5Za#sGe(=ryZ(d9G-7mcDovrS;^9`6)mL|ld@tA@NcTQJqww"
    b"BF2rmG{*uZ7jfQwAgo)uHcaV)|N9DH5b_l5U|4Sn&1Xp3V<s$lnz6M1%^#T%VfpwhElo*yq#nnC3"
    b"_p=SrXn>eqc{YJ8d90#;k3Gw0ZB>h5y9sK>Rs6q~(eY=={PDFA(%uC^6^$j8^l`#%2*(g_qRAOH^"
    b"eb}iU^DlL`OurQSy!5Nz&^o{go7wNJE!mcQF0@_h##GON^%eg*d;2zDifx1W$T2UHjl4O)^s{5x="
    b"?WG@u-ER;<;p{n^;BqBr9Yl^2g(FL2lZ+SRRL(srRuQGggyu3YA#j*XPcDOA-*U3S@<KfvD+khb;"
    b"y@;t+!ukh<|V-uBeNL2S@lyhw#ctA`*oRYV5Cke0rW^hEgO^_GB$Wl_>NT>!!%CTGfpx^o8q2m*i"
    b"v#SgV@ZiG0O+DH60+H6sdbLpQ?QpiVg-?8j15YFK222DKcuJEpeJbN^sIIale)dwQ}q5mj=S_{-2"
    b"vU;B=%z@Tz`U2~-z9EJYU%w_NFH@Q+&2N*Wjjr>gcm!_iZJI+^!A72uQ$WH8RuAb^C{+drh*VE0x"
    b"LV)~Oh`l8k?V?HXVTX#A;45z%8eaGaw&ky~KO7cwUITH-cb7%#>3Ym1V)yV_VDUxh*GGP6F_>nYv"
    b"roAEzo7aN-_sbsVyfb2@5Q|}3cg>+5)3)}T!oJH=t&n|_I4vw*Iv#jW637Q3!B*li$!|1+(+Zum+"
    b"n!}P_>#RtiyGGmhfee&GHF2x1w#bJHao=ijC8tb(rSUpx=M}2XmNGk^"
)


def _payload_size_kb() -> float:
    """Size of the embedded compressed payload (KB)."""
    return len(_PAYLOAD) / 1024.0


try:
    DATA = json.loads(zlib.decompress(base64.b85decode(_PAYLOAD)))
except (ValueError, zlib.error) as _e:
    raise ImportError("raw_craft: corrupted embedded payload (re-run compiler.py)") from _e
