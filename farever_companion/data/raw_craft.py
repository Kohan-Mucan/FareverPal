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
    b"c-rM%?Q+|?(Y_Upey52<SxV}EwqwV68=q(_pLphUG8#yNB-A9K7bNW{XEM{<=<W4Ny5N@}5uYUe)"
    b"&5E>l8fhA>|%ET5d5<{od!5Y(`onk&+eQC8{%WuJud(IcC+oi=`Il5a*tU=Xb2a*o;UhS@55hu-c"
    b"A4g@vwK?|Lfac|NY?T=#TENH{E~GS=2rL`=8y_@&^~Y1VQlB@1Ng|eKf~w>Mt;R_jyjkXnKt!ny@"
    b"*J-fqGbw6w-#wT`>T-kWYje!^o%AKr8y$Rb{+zk$5L=uLME|9pU!$O7bEk&vX%GCU_6d_@=7=k0%"
    b"g(~a?uIN}f%IGV$)pHRHUtV0>J2M>fO!2IgQPZ;tuQ9u%|+!udze?c=brT)!!!%2L?h>gko4*kGz"
    b"Te4X(1hRQ+zur6*;Y-YB$R|<Y!g=GPFqX3}<i!Q^@&_6F39$E{kNgFR){fo1ghvz|&ZS`%;4o*N|"
    b"Bb?z*zYH7zQ)w2tE~%r#A32Ub4A~mGKKCJe`~4-%3_8{9KHJ-;~;u>1CmZbH+NA$;&t}p^b4i&lw"
    b"XOamnaA@oA&x|Hw-V)ELT>hA#XTJ71K-g(ks-mlUG&CpWT4}7w*d1=S{u4PVdS8neHCX+vHLZBQd"
    b"nmJ)=PYLmubWOwjM)ye(1Jh~XB*oNvgy1u<Xn3Wp2Cw%uP68~i==F^bk5u*A;N2jh!6blDki8T>>"
    b"$GkCJY!;jDZ+Jb*{9*FOEQ2MhUcNlu$gLQ|5aGlXv62%?l`*a(<?Oaf{!X1Wvf@VJd4+)nP91NT}"
    b"j#3e8jPAE!{{dwH0L7g-3Za2`9mV`I#}rVIxFncuJ2Mo=zG9fzX`t{C!RUS!R`)UEe1j4APsI6j*"
    b"7Si>n=`bGJIBWaRD68=wzSjV(uTS)T7<{t<?g=6?Ym4}r#tZhQveQK^|>p!9Xa}3DXx~wl}|VHIG"
    b"r3fhto;2_cxssdw0`G(R<q^Vj+&A)UwxPvA|(xLmzm;v&`dv>KFLO#^+weZ;SAp;W+h5{wFvU4k("
    b"Ws;9*~2tPi>QLNg2^NP5Qt;|&I<+0YmqjezUIqalj9r+}ZX7+#Rl180=`RENZw2j9c(X;Yg7nul9"
    b"a%<;+tQ=x@2DwQVJ+~G1!Yh6yX@LC@CHHnubj(Gxb=y8)di6Y7(H=ip7*7CXRX`awGayg9{H;>Z@"
    b"vUfO*5PN^q2+_OSxsPU)p*ZuT4Q5LWP6A4p5pW17mAI(~aGj-g4`vx70-o1dA-0QBOF;Aho(5qEL"
    b"Qxv3a^K)?2A)V2L8-Kd!b5fj(n3f=T#!C0i3VBAMg9*zVBeQ{$k-=*2(ovMLR2l^C?0O;8yYcgzM"
    b"&Ch?;9E+_P(JJqW6t!e2;lr$0G*0he{f9xdlPt97{sYzyJBKEE6L^faZzMU24v@kf!+%hQ<_1(KL"
    b"k&QXfi#+iK0dV}Zd@Qi%W$8sT1*g}88zqSP-5gV+Ptz>4!=n1&*{Nql}YgOHW^;R5~q>BC>05JQ2"
    b"B<_fP%HL~-${LiOI0tFMV<!xt_-c>#AZy{M?2S?*lYB?Eyu%Uw~M7TMZLWsR%DFoO%l|q0i*x>sj"
    b")IedwvYZX^YH4u~mJAsci_+>Qt$T3jZz12B$4Tl#`CI_h)==rp8GTrQ(KDKW-KS-jkM6mTEumP8q"
    b"c@#pf-~Qp(Z!axlZBxyUJ=OMl1=VgFsb27{e+V&oFuiGC&}7!>#XG;=iz(}IkGGq-2!p9WUUld%U"
    b"8;$8#;_mj+@u$B-y);PKv$%=%g5&NGN0iv?6l|QBZ*3JS?U0VVU@y70<`Gu|w!cqFn)lLhy#!KOo"
    b"I}+c`_~yv!k?qQWrE7Nt@mJ`0h0BbUIZ-y#&I=^xt##Ug_3l~LRyI{5z+vf+VD>Q*K9!nbyY*4)7"
    b"$BnJ?EwH_uK_MV%4FN9?_K8JYTJ$^p~e+V~8od1@D@IRR7<l<9X-hkphhbtPVU7z33AC`AqkZ}4("
    b"fA8Y%8bd3tsISXglq%qRdFd8G<u<*(LLs00on8>`u&^$1I9&*XDz`O|8Tpi(N-t|kiM`)5f%kTIT"
    b"%d4ef&Lt9WG$jU<EAma{u`aa3^E6Q5=HL#`@NowfM+5wC~uh1Wqgq^1-n~PVdqKM{l@18ut@I|2u"
    b"!a}G3L~6$<OH-H%l!+&w=33rH5S1OLo&3MhagfcL`1(?O7glUTub+-hWOR4S!;-|5QzIcQG@tD?R"
    b"~h>cxtUUSPauqGvv~U^#dkmJ(CkUHs(JDB7N_NlZd_W6ejBowe5BH#9h+g5ZwAH40Xs=c@kC7zrX"
    b"8<{hAgHgtey)cpq?w0!syuvu7!6rR_s2d_UW@AwEsW=Q6jlo>n!Y>jBN=gSx^rq`(iOBSPj78U4+"
    b"W7F3>bE=FhlMP~b6BKOxJ!=$4G($k}A#vh@0;mzr#{I4}vYP}-V#rFcE4cDOGd|CRPv|BMQW7dLs"
    b"!|IR3DKeqp6;mOzR7BZz#G#LCMFqH2B!mE3j|L1`WSyshyJKUe+nbVIJ@1_KCUT*Zxt>xa)sV+*R"
    b"Tx1AGWmX7lS)p$8Q07HsVRg<3Y7^1q9c{%I6}p`7xhAGxdHeEnfoZI*mf*5c<#?lXOQ!S=mo3@|x"
    b")-X%}5ZUL$l9m<xrC8KhN+a-ofIUcodhnYW0sDzFj0Pf*ZfsM%XYC==NT<HvLcyv>f5NHi3#yPW9"
    b";h43gW?(!+YcFXQ8DJ^OrD155bow#V^Tz&6Id;$95DfJ!d9;?zjFW>UIrdrh4!Z#M*%3knv<#%5@"
    b"xnNbe<poX75c<f9q*sfiw3oEyw^w6!$I8eY>TAJ_)V|^w>&u~b9>uEo%quBgeA+sbLxn9)1;Hchg"
    b"RU4@8e+5hIvX-LL!f76iY_&_MNb~-Xddb6+-x4JD06K+$)OGxJVn+)BOP*=3f)%o^=kBbnimZ08-"
    b"{h81)t+_uHYwYSf7GTxKUBYS102nJV&*7%DPBTaU|%7_!FPZaickklvv)I4*WFPjC24WsJiz1!E<"
    b"P3h?})*IVmm+XP1m=qCZ1G^$bIgBV<9985-C6a-WKoI&CK;23pfKBHQ&rtElw(k40pb8HwO8WPxX"
    b"V;ro`alIx%!wNkMkv?s0G&x0AGa39+m@U+-^T5CHutgYMyX>F({X%pa~v=#vFitW}=N=3E+HLJN>"
    b"gIN*R0>q-|ZVjYFU<)ww!n-w;Re7cbXycTnrV5mn%2#N?2+t75*+Ss={T9A1$9!8v$y3}I?Y?|o3"
    b"w=>FNa26so1GT0Z9xV9ED7@$1fPvSbphEy{kJ4q(BL`YSFNXKul{R3KaOb<@vZ*OEaX!RE{j$93c"
    b"b}lkp3o#8QRQNjMuQ~JE9xNQzm=M1QH#%%u)!|9D!-T#Z403$<yJoq4R97lX9uNEET1SWbn!<>}#"
    b"JFTt$+f(~VLY1`R)l+Fh7F*$~*>b;&n9^8Ea{D2@}BhUOHAj^vTU1clm6pO#y3F@s@GP7_4+>uvI"
    b"y;tICI72c>HDr(m{r|{&W9<F=<cqCxDId?6rY{Y35m8T3(vPf|l17!D{gmeC0*v)zy;L2@Aq8ISS"
    b"{`$h_i3nsP3q9Q?OY_x+q%i-0eVjB-A{`UhY8tNaWW!Gz*k7Qpy0aA<_c;gE)U2gposgFGEX+7iM"
    b"+?XhT@bzhHS78!gr}`MUB<xG?79O{yEm*l)Hks&O~oYi8sQywhG4dpQH8CY`{V~<Uzgw+I9%}wCr"
    b"^LlN7%lN`NnSJou6SxvhR~s=1+#3FJR-px^Zc=6|!1xSLHn=VU2=^))x4UsGLGQ3imBWVe8<h8nW"
    b"Oh6v`m);qOO{DSx%mF5K9TSZWmvEL1SCTlH14E9@d=Ss3TbBq-VF>PoP5s;6gYW1-qC$cmsi7Q9T"
    b"3=Ud#6V>*OA6}h&OLEF+muK}8A;DjIRVJM~58c<EgNt$>jWqF2$BhPv#vB{uLW-!r-49?O(n_>%("
    b"5CA=wJ)GJAH(3)54H)BFEnQSwc!VKl)NC02Lc`T5KY3vNNJ^+QP}?3X>omJmT&a-HA-on#X2geZX"
    b";#Tm9hFF$cpRk+>~@YBhYK-#ud9s5Vdyz*Nix#ht36&S&)@JSB8v&U#)@mwJS?1EDpU0wumjn$@O"
    b"xP-o#q7eA?zE5G3N0GD_I+ltrLZfH>?;g;Gl!a0|_2UQ3v9m&TcBxQV#Bs=DP;>7&giSiw2%i9~Q"
    b"PDW@(S1jzjsv9z*>?UyFLl_Yu}elJoFyb{zRG%9qcujQsz-6w^TX-%C$SYMfcN&uwUW8QEq`LUX?"
    b"QY{{p_m~<fju{9yz(`})MZ~VL#|B<w?C)}`==5PXLg5y|2M?N(nyf+ri4&J2#Vd<>Tb+)Y=HtUys"
    b"g`>cnw*a+_`xN%9`8wPe!eZ?U!fn*|$>vq6`^p)%e2dkQ8Mn*(>Xma+e#=kUE0H_H_G+qo;9@1K^"
    b"wr9$y1kuOo{Tln2hRrmI+<;C$iXkK+>sA!+KS~SrQ9oJmvYOOUCO9R<*jzF##UyO!Tex`2+OU~Gg"
    b"_s~i(GAVt@w5>%>TlhkFbtpe_l-9{R69TGJ4hA^daYUbJK>U*G=upiCQYZXLCeby+d}!j7A$c=}+"
    b"7`E)7k2nkjqA9dYx+cU*BR9;^Xfa~h8dGzsnC?lvh^9?{osQd{`0uzeeKxo>r=vk&$xiTC%4S0Q-"
    b"~wz^fq?4a86KAP+a8#M0I=9S29M3X)7IB0~tzyZ<jDH|hpM^#B=%ssY3S~N`-It~*qBbfAo23V~2"
    b"AeLsj_Yw;1&Rf|Ta4QY+?>vQYK&o4l%GVYTr8QFqY{P()X&3Eat4M2zjjf1lPpP5p3v)qMsjzQwu"
    b"*rE^j5{W16={vIoy2i^Dpf1c4uv&B_KCo`3oQXVkmY1&hXwp@KroMwwFZR421OUPZ_;K!SXmrQWl"
    b")CT!p*T&q&37=`fwZ2_Jz41JDf-nJlGu89EJlSsMZ6v^WWyU4rDcfReEo8RQsZ=U*}d7r+u6ARX)"
    b"-n|C-NP!lJZsF2<@*Rlf8sm-!@oPFVt*24QuxJvwrcPr~LKtXQ>2q$2G}IKXy1u!SgSkB3yE;Y1z"
    b"%n<4raE95ooWx_ubFhPm1vpD}gM+>JE>+!;b((DTMnYv;wzyI1UY`2#@)cbpl_$PN1KGXb>rLQTo"
    b"S)^cKar|;roKkSeWn{1918wfjyU@GoK&@YAER(w=ywida&G8V)s4<eZw*B|jU3m!lgSvCwyK;?jq"
    b"}exb!gACimQ#vY?VjwLfv85!{CR7fx71(O#_2W0>DlAtXESZ<ckTOzNZ;Edy@CT3AsO~-jV8l}IE"
    b"MnxK~L{3bPI1I^S*cQ_`fP(N|##=I3^Epc;+=#o^WsK+J3Jw7;%jVcIfh#I~J<94r*wtukz=k8VK"
    b"Qtd89bVaGV62JLNm_Mm>5hcIq|VBKX#y?{Dj(8JKmZ9|H#tu8;=~uSVY<HN|;kk8@1!aIImbKWvK"
    b"9Wnh7f>U_1T2XxdF=*R?AeBIv%BfMc~tNQvzI@GDJ!O)|**w<ZDy#29fYSQzpl={2ipFLD)Z)okp"
    b"efw(A=)>80uBzdARAX2%ZavkxY6hoohNCsGLW=c(ZQ3Mi@QAEEuNBmuy%S5%@^$X}K`XEa_R3Zm$"
    b"iSAD*?x{%p>*_gl&tJ)*b1v*dtPB_V53%Wk4*DQuwX$J365N6V)E}yW_!rRBa*}qP}V=+YPT<B%)"
    b"ih%_bbJ3Px<blIGM^1H~a=CcYb9v9YXsvV8&=udDnkXnMRBbPNgZdRXp6Pvij;yk?#fD1L@x9XB6"
    b"Js2;l{Q7#p|@8*q^++OuLRIgUk`frGCr`@Zo0u%R!W5N&bnNEPh<aU#zSw0NVl3RHTw%k$$!N;9Y"
    b"&C-m%KcN6DTwBJa!kmG>XDj_}`o}^z$u!Fz96;;ULvu_vk&xuIRhj}SHtiC}cr835$B^%|ckV@<e"
    b"rw=>TWB#S?mg$#bpO=318(IfAVKe)^wI$n<&xN*2-I(e*u)gA7XK&x;OR;S*A35elvXM6jn0|IFJ"
    b"6*wI2=>|?x?FikvYrYJtMtdS?m(H~=b&Cqp(+54xl|q2nb7B8T+PKQ;B1q!I>h&6KMQ|kHg=mNuM"
    b"YWAuJ!gS4e<|-#1(b%@Zbnv=@4EyNkpQML5=;Dk5A8Y%utFX%ATx6s1k*~V%-!OR;#)Zg}!jz=#r"
    b"c)0c%w=JCUzgFGYgZs%Au?ZRkdr8`LT1gZ)@)N)3yw(4dy1cE{BAdF~&~C+8|Xc~4K)CZb9X6Mq@"
    b"n>1*GS6&O^GuWPQ9gQL)2nz`(q{U%q6>Hx#$x{;qL|I$>HeaHFg@Z(D&5!s2I&DB#q#b5JD=z6+2"
    b"SO}0G3$eEZqg^y9KI~A@5`5)tL&FOn!M6NW_J_k_&TAkp`R=kvJzbBvMC=|u3oO0}{rbocEe6w!b"
    b"M^_B{|Txe@jZ?4E2b)b_Fmjuqu~36EWwbo&sFGHkDhelWp6h^b?xPhGL~#&ys()~uvny5%Y8Jied"
    b"!(r4OOdI!a7{{X9-^h*({%cb1T{=yA%9^tk^gWT8C*q4f_4pe*h)fQkD"
)


def _payload_size_kb() -> float:
    """Size of the embedded compressed payload (KB)."""
    return len(_PAYLOAD) / 1024.0


try:
    DATA = json.loads(zlib.decompress(base64.b85decode(_PAYLOAD)))
except (ValueError, zlib.error) as _e:
    raise ImportError("raw_craft: corrupted embedded payload (re-run compiler.py)") from _e
