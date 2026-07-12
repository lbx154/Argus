import{R as Ya,r as Jt}from"./query-BpxuCIcN.js";/*!
 * Font Awesome Free 7.3.0 by @fontawesome - https://fontawesome.com
 * License - https://fontawesome.com/license/free (Icons: CC BY 4.0, Fonts: SIL OFL 1.1, Code: MIT License)
 * Copyright 2026 Fonticons, Inc.
 */function Ea(a,n){(n==null||n>a.length)&&(n=a.length);for(var t=0,e=Array(n);t<n;t++)e[t]=a[t];return e}function Qt(a){if(Array.isArray(a))return a}function Zt(a){if(Array.isArray(a))return Ea(a)}function ae(a,n){if(!(a instanceof n))throw new TypeError("Cannot call a class as a function")}function ne(a,n){for(var t=0;t<n.length;t++){var e=n[t];e.enumerable=e.enumerable||!1,e.configurable=!0,"value"in e&&(e.writable=!0),Object.defineProperty(a,$n(e.key),e)}}function te(a,n,t){return n&&ne(a.prototype,n),Object.defineProperty(a,"prototype",{writable:!1}),a}function ua(a,n){var t=typeof Symbol<"u"&&a[Symbol.iterator]||a["@@iterator"];if(!t){if(Array.isArray(a)||(t=Xa(a))||n){t&&(a=t);var e=0,r=function(){};return{s:r,n:function(){return e>=a.length?{done:!0}:{done:!1,value:a[e++]}},e:function(f){throw f},f:r}}throw new TypeError(`Invalid attempt to iterate non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`)}var i,o=!0,s=!1;return{s:function(){t=t.call(a)},n:function(){var f=t.next();return o=f.done,f},e:function(f){s=!0,i=f},f:function(){try{o||t.return==null||t.return()}finally{if(s)throw i}}}}function g(a,n,t){return(n=$n(n))in a?Object.defineProperty(a,n,{value:t,enumerable:!0,configurable:!0,writable:!0}):a[n]=t,a}function ee(a){if(typeof Symbol<"u"&&a[Symbol.iterator]!=null||a["@@iterator"]!=null)return Array.from(a)}function re(a,n){var t=a==null?null:typeof Symbol<"u"&&a[Symbol.iterator]||a["@@iterator"];if(t!=null){var e,r,i,o,s=[],f=!0,u=!1;try{if(i=(t=t.call(a)).next,n===0){if(Object(t)!==t)return;f=!1}else for(;!(f=(e=i.call(t)).done)&&(s.push(e.value),s.length!==n);f=!0);}catch(m){u=!0,r=m}finally{try{if(!f&&t.return!=null&&(o=t.return(),Object(o)!==o))return}finally{if(u)throw r}}return s}}function ie(){throw new TypeError(`Invalid attempt to destructure non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`)}function oe(){throw new TypeError(`Invalid attempt to spread non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`)}function en(a,n){var t=Object.keys(a);if(Object.getOwnPropertySymbols){var e=Object.getOwnPropertySymbols(a);n&&(e=e.filter(function(r){return Object.getOwnPropertyDescriptor(a,r).enumerable})),t.push.apply(t,e)}return t}function l(a){for(var n=1;n<arguments.length;n++){var t=arguments[n]!=null?arguments[n]:{};n%2?en(Object(t),!0).forEach(function(e){g(a,e,t[e])}):Object.getOwnPropertyDescriptors?Object.defineProperties(a,Object.getOwnPropertyDescriptors(t)):en(Object(t)).forEach(function(e){Object.defineProperty(a,e,Object.getOwnPropertyDescriptor(t,e))})}return a}function pa(a,n){return Qt(a)||re(a,n)||Xa(a,n)||ie()}function O(a){return Zt(a)||ee(a)||Xa(a)||oe()}function se(a,n){if(typeof a!="object"||!a)return a;var t=a[Symbol.toPrimitive];if(t!==void 0){var e=t.call(a,n);if(typeof e!="object")return e;throw new TypeError("@@toPrimitive must return a primitive value.")}return(n==="string"?String:Number)(a)}function $n(a){var n=se(a,"string");return typeof n=="symbol"?n:n+""}function da(a){"@babel/helpers - typeof";return da=typeof Symbol=="function"&&typeof Symbol.iterator=="symbol"?function(n){return typeof n}:function(n){return n&&typeof Symbol=="function"&&n.constructor===Symbol&&n!==Symbol.prototype?"symbol":typeof n},da(a)}function Xa(a,n){if(a){if(typeof a=="string")return Ea(a,n);var t={}.toString.call(a).slice(8,-1);return t==="Object"&&a.constructor&&(t=a.constructor.name),t==="Map"||t==="Set"?Array.from(a):t==="Arguments"||/^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t)?Ea(a,n):void 0}}var rn=function(){},Ha={},Mn={},Dn=null,Rn={mark:rn,measure:rn};try{typeof window<"u"&&(Ha=window),typeof document<"u"&&(Mn=document),typeof MutationObserver<"u"&&(Dn=MutationObserver),typeof performance<"u"&&(Rn=performance)}catch{}var fe=Ha.navigator||{},on=fe.userAgent,sn=on===void 0?"":on,$=Ha,x=Mn,fn=Dn,oa=Rn;$.document;var _=!!x.documentElement&&!!x.head&&typeof x.addEventListener=="function"&&typeof x.createElement=="function",Wn=~sn.indexOf("MSIE")||~sn.indexOf("Trident/"),sa,le=/fa(k|kd|s|r|l|t|d|dr|dl|dt|b|slr|slpr|wsb|tl|ns|nds|es|gt|jr|jfr|jdr|usb|ufsb|udsb|cr|ss|sr|sl|st|sds|sdr|sdl|sdt|sldr|slpdr|pr|ms|vs)?[\-\ ]/,ue=/Font ?Awesome ?([567 ]*)(Solid|Regular|Light|Thin|Duotone|Brands|Free|Pro|Sharp Duotone|Sharp|Kit|Notdog Duo|Notdog|Chisel|Etch|Graphite|Thumbprint|Jelly Fill|Jelly Duo|Jelly|Utility|Utility Fill|Utility Duo|Slab Press|Slab|Slab Duo|Slab Press Duo|Pixel|Mosaic|Vellum|Whiteboard)?.*/i,Un={classic:{fa:"solid",fas:"solid","fa-solid":"solid",far:"regular","fa-regular":"regular",fal:"light","fa-light":"light",fat:"thin","fa-thin":"thin",fab:"brands","fa-brands":"brands"},duotone:{fa:"solid",fad:"solid","fa-solid":"solid","fa-duotone":"solid",fadr:"regular","fa-regular":"regular",fadl:"light","fa-light":"light",fadt:"thin","fa-thin":"thin"},sharp:{fa:"solid",fass:"solid","fa-solid":"solid",fasr:"regular","fa-regular":"regular",fasl:"light","fa-light":"light",fast:"thin","fa-thin":"thin"},"sharp-duotone":{fa:"solid",fasds:"solid","fa-solid":"solid",fasdr:"regular","fa-regular":"regular",fasdl:"light","fa-light":"light",fasdt:"thin","fa-thin":"thin"},slab:{"fa-regular":"regular",faslr:"regular"},"slab-press":{"fa-regular":"regular",faslpr:"regular"},"slab-duo":{"fa-regular":"regular",fasldr:"regular"},"slab-press-duo":{"fa-regular":"regular",faslpdr:"regular"},thumbprint:{"fa-light":"light",fatl:"light"},vellum:{"fa-solid":"solid",favs:"solid"},pixel:{"fa-regular":"regular",fapr:"regular"},mosaic:{"fa-solid":"solid",fams:"solid"},whiteboard:{"fa-semibold":"semibold",fawsb:"semibold"},notdog:{"fa-solid":"solid",fans:"solid"},"notdog-duo":{"fa-solid":"solid",fands:"solid"},etch:{"fa-solid":"solid",faes:"solid"},graphite:{"fa-thin":"thin",fagt:"thin"},jelly:{"fa-regular":"regular",fajr:"regular"},"jelly-fill":{"fa-regular":"regular",fajfr:"regular"},"jelly-duo":{"fa-regular":"regular",fajdr:"regular"},chisel:{"fa-regular":"regular",facr:"regular"},utility:{"fa-semibold":"semibold",fausb:"semibold"},"utility-duo":{"fa-semibold":"semibold",faudsb:"semibold"},"utility-fill":{"fa-semibold":"semibold",faufsb:"semibold"}},ce={GROUP:"duotone-group",PRIMARY:"primary",SECONDARY:"secondary"},Yn=["fa-classic","fa-duotone","fa-sharp","fa-sharp-duotone","fa-thumbprint","fa-whiteboard","fa-notdog","fa-notdog-duo","fa-chisel","fa-etch","fa-graphite","fa-jelly","fa-jelly-fill","fa-jelly-duo","fa-slab","fa-slab-press","fa-slab-press-duo","fa-slab-duo","fa-mosaic","fa-pixel","fa-vellum","fa-utility","fa-utility-duo","fa-utility-fill"],P="classic",ea="duotone",Xn="sharp",Hn="sharp-duotone",Gn="chisel",Vn="etch",Bn="graphite",qn="jelly",Kn="jelly-duo",Jn="jelly-fill",Qn="mosaic",Zn="notdog",at="notdog-duo",nt="pixel",tt="slab",et="slab-duo",rt="slab-press",it="slab-press-duo",ot="thumbprint",st="utility",ft="utility-duo",lt="utility-fill",ut="vellum",ct="whiteboard",me="Classic",de="Duotone",ge="Sharp",ve="Sharp Duotone",pe="Chisel",be="Etch",he="Graphite",ye="Jelly",xe="Jelly Duo",Se="Jelly Fill",we="Mosaic",Ae="Notdog",ke="Notdog Duo",Ie="Pixel",Pe="Slab",ze="Slab Duo",Ee="Slab Press",Fe="Slab Press Duo",Oe="Thumbprint",Ce="Utility",je="Utility Duo",Ne="Utility Fill",Te="Vellum",_e="Whiteboard",mt=[P,ea,Xn,Hn,Gn,Vn,Bn,qn,Kn,Jn,Qn,Zn,at,nt,tt,et,rt,it,ot,st,ft,lt,ut,ct];sa={},g(g(g(g(g(g(g(g(g(g(sa,P,me),ea,de),Xn,ge),Hn,ve),Gn,pe),Vn,be),Bn,he),qn,ye),Kn,xe),Jn,Se),g(g(g(g(g(g(g(g(g(g(sa,Qn,we),Zn,Ae),at,ke),nt,Ie),tt,Pe),et,ze),rt,Ee),it,Fe),ot,Oe),st,Ce),g(g(g(g(sa,ft,je),lt,Ne),ut,Te),ct,_e);var Le={classic:{900:"fas",400:"far",normal:"far",300:"fal",100:"fat"},duotone:{900:"fad",400:"fadr",300:"fadl",100:"fadt"},sharp:{900:"fass",400:"fasr",300:"fasl",100:"fast"},"sharp-duotone":{900:"fasds",400:"fasdr",300:"fasdl",100:"fasdt"},slab:{400:"faslr"},"slab-press":{400:"faslpr"},"slab-duo":{400:"fasldr"},"slab-press-duo":{400:"faslpdr"},vellum:{900:"favs"},mosaic:{900:"fams"},pixel:{400:"fapr"},whiteboard:{600:"fawsb"},thumbprint:{300:"fatl"},notdog:{900:"fans"},"notdog-duo":{900:"fands"},etch:{900:"faes"},graphite:{100:"fagt"},chisel:{400:"facr"},jelly:{400:"fajr"},"jelly-fill":{400:"fajfr"},"jelly-duo":{400:"fajdr"},utility:{600:"fausb"},"utility-duo":{600:"faudsb"},"utility-fill":{600:"faufsb"}},$e={"Font Awesome 7 Free":{900:"fas",400:"far"},"Font Awesome 7 Pro":{900:"fas",400:"far",normal:"far",300:"fal",100:"fat"},"Font Awesome 7 Brands":{400:"fab",normal:"fab"},"Font Awesome 7 Duotone":{900:"fad",400:"fadr",normal:"fadr",300:"fadl",100:"fadt"},"Font Awesome 7 Sharp":{900:"fass",400:"fasr",normal:"fasr",300:"fasl",100:"fast"},"Font Awesome 7 Sharp Duotone":{900:"fasds",400:"fasdr",normal:"fasdr",300:"fasdl",100:"fasdt"},"Font Awesome 7 Jelly":{400:"fajr",normal:"fajr"},"Font Awesome 7 Jelly Fill":{400:"fajfr",normal:"fajfr"},"Font Awesome 7 Jelly Duo":{400:"fajdr",normal:"fajdr"},"Font Awesome 7 Slab":{400:"faslr",normal:"faslr"},"Font Awesome 7 Slab Press":{400:"faslpr",normal:"faslpr"},"Font Awesome 7 Slab Duo":{400:"fasldr",normal:"fasldr"},"Font Awesome 7 Slab Press Duo":{400:"faslpdr",normal:"faslpdr"},"Font Awesome 7 Pixel":{400:"fapr",normal:"fapr"},"Font Awesome 7 Mosaic":{900:"fams",normal:"fams"},"Font Awesome 7 Vellum":{900:"favs",normal:"favs"},"Font Awesome 7 Thumbprint":{300:"fatl",normal:"fatl"},"Font Awesome 7 Notdog":{900:"fans",normal:"fans"},"Font Awesome 7 Notdog Duo":{900:"fands",normal:"fands"},"Font Awesome 7 Etch":{900:"faes",normal:"faes"},"Font Awesome 7 Graphite":{100:"fagt",normal:"fagt"},"Font Awesome 7 Chisel":{400:"facr",normal:"facr"},"Font Awesome 7 Whiteboard":{600:"fawsb",normal:"fawsb"},"Font Awesome 7 Utility":{600:"fausb",normal:"fausb"},"Font Awesome 7 Utility Duo":{600:"faudsb",normal:"faudsb"},"Font Awesome 7 Utility Fill":{600:"faufsb",normal:"faufsb"}},Me=new Map([["classic",{defaultShortPrefixId:"fas",defaultStyleId:"solid",styleIds:["solid","regular","light","thin","brands"],futureStyleIds:[],defaultFontWeight:900}],["duotone",{defaultShortPrefixId:"fad",defaultStyleId:"solid",styleIds:["solid","regular","light","thin"],futureStyleIds:[],defaultFontWeight:900}],["sharp",{defaultShortPrefixId:"fass",defaultStyleId:"solid",styleIds:["solid","regular","light","thin"],futureStyleIds:[],defaultFontWeight:900}],["sharp-duotone",{defaultShortPrefixId:"fasds",defaultStyleId:"solid",styleIds:["solid","regular","light","thin"],futureStyleIds:[],defaultFontWeight:900}],["chisel",{defaultShortPrefixId:"facr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["etch",{defaultShortPrefixId:"faes",defaultStyleId:"solid",styleIds:["solid"],futureStyleIds:[],defaultFontWeight:900}],["graphite",{defaultShortPrefixId:"fagt",defaultStyleId:"thin",styleIds:["thin"],futureStyleIds:[],defaultFontWeight:100}],["jelly",{defaultShortPrefixId:"fajr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["jelly-duo",{defaultShortPrefixId:"fajdr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["jelly-fill",{defaultShortPrefixId:"fajfr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["mosaic",{defaultShortPrefixId:"fams",defaultStyleId:"solid",styleIds:["solid"],futureStyleIds:[],defaultFontWeight:900}],["notdog",{defaultShortPrefixId:"fans",defaultStyleId:"solid",styleIds:["solid"],futureStyleIds:[],defaultFontWeight:900}],["notdog-duo",{defaultShortPrefixId:"fands",defaultStyleId:"solid",styleIds:["solid"],futureStyleIds:[],defaultFontWeight:900}],["pixel",{defaultShortPrefixId:"fapr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["slab",{defaultShortPrefixId:"faslr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["slab-duo",{defaultShortPrefixId:"fasldr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["slab-press",{defaultShortPrefixId:"faslpr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["slab-press-duo",{defaultShortPrefixId:"faslpdr",defaultStyleId:"regular",styleIds:["regular"],futureStyleIds:[],defaultFontWeight:400}],["thumbprint",{defaultShortPrefixId:"fatl",defaultStyleId:"light",styleIds:["light"],futureStyleIds:[],defaultFontWeight:300}],["utility",{defaultShortPrefixId:"fausb",defaultStyleId:"semibold",styleIds:["semibold"],futureStyleIds:[],defaultFontWeight:600}],["utility-duo",{defaultShortPrefixId:"faudsb",defaultStyleId:"semibold",styleIds:["semibold"],futureStyleIds:[],defaultFontWeight:600}],["utility-fill",{defaultShortPrefixId:"faufsb",defaultStyleId:"semibold",styleIds:["semibold"],futureStyleIds:[],defaultFontWeight:600}],["vellum",{defaultShortPrefixId:"favs",defaultStyleId:"solid",styleIds:["solid"],futureStyleIds:[],defaultFontWeight:900}],["whiteboard",{defaultShortPrefixId:"fawsb",defaultStyleId:"semibold",styleIds:["semibold"],futureStyleIds:[],defaultFontWeight:600}]]),De={chisel:{regular:"facr"},classic:{brands:"fab",light:"fal",regular:"far",solid:"fas",thin:"fat"},duotone:{light:"fadl",regular:"fadr",solid:"fad",thin:"fadt"},etch:{solid:"faes"},graphite:{thin:"fagt"},jelly:{regular:"fajr"},"jelly-duo":{regular:"fajdr"},"jelly-fill":{regular:"fajfr"},mosaic:{solid:"fams"},notdog:{solid:"fans"},"notdog-duo":{solid:"fands"},pixel:{regular:"fapr"},sharp:{light:"fasl",regular:"fasr",solid:"fass",thin:"fast"},"sharp-duotone":{light:"fasdl",regular:"fasdr",solid:"fasds",thin:"fasdt"},slab:{regular:"faslr"},"slab-duo":{regular:"fasldr"},"slab-press":{regular:"faslpr"},"slab-press-duo":{regular:"faslpdr"},thumbprint:{light:"fatl"},utility:{semibold:"fausb"},"utility-duo":{semibold:"faudsb"},"utility-fill":{semibold:"faufsb"},vellum:{solid:"favs"},whiteboard:{semibold:"fawsb"}},dt=["fak","fa-kit","fakd","fa-kit-duotone"],ln={kit:{fak:"kit","fa-kit":"kit"},"kit-duotone":{fakd:"kit-duotone","fa-kit-duotone":"kit-duotone"}},Re=["kit"],We="kit",Ue="kit-duotone",Ye="Kit",Xe="Kit Duotone";g(g({},We,Ye),Ue,Xe);var He={kit:{"fa-kit":"fak"}},Ge={"Font Awesome Kit":{400:"fak",normal:"fak"},"Font Awesome Kit Duotone":{400:"fakd",normal:"fakd"}},Ve={kit:{fak:"fa-kit"}},un={kit:{kit:"fak"},"kit-duotone":{"kit-duotone":"fakd"}},fa,la={GROUP:"duotone-group",SWAP_OPACITY:"swap-opacity",PRIMARY:"primary",SECONDARY:"secondary"},Be=["fa-classic","fa-duotone","fa-sharp","fa-sharp-duotone","fa-thumbprint","fa-whiteboard","fa-notdog","fa-notdog-duo","fa-chisel","fa-etch","fa-graphite","fa-jelly","fa-jelly-fill","fa-jelly-duo","fa-slab","fa-slab-press","fa-slab-press-duo","fa-slab-duo","fa-mosaic","fa-pixel","fa-vellum","fa-utility","fa-utility-duo","fa-utility-fill"],qe="classic",Ke="duotone",Je="sharp",Qe="sharp-duotone",Ze="chisel",ar="etch",nr="graphite",tr="jelly",er="jelly-duo",rr="jelly-fill",ir="mosaic",or="notdog",sr="notdog-duo",fr="pixel",lr="slab",ur="slab-duo",cr="slab-press",mr="slab-press-duo",dr="thumbprint",gr="utility",vr="utility-duo",pr="utility-fill",br="vellum",hr="whiteboard",yr="Classic",xr="Duotone",Sr="Sharp",wr="Sharp Duotone",Ar="Chisel",kr="Etch",Ir="Graphite",Pr="Jelly",zr="Jelly Duo",Er="Jelly Fill",Fr="Mosaic",Or="Notdog",Cr="Notdog Duo",jr="Pixel",Nr="Slab",Tr="Slab Duo",_r="Slab Press",Lr="Slab Press Duo",$r="Thumbprint",Mr="Utility",Dr="Utility Duo",Rr="Utility Fill",Wr="Vellum",Ur="Whiteboard";fa={},g(g(g(g(g(g(g(g(g(g(fa,qe,yr),Ke,xr),Je,Sr),Qe,wr),Ze,Ar),ar,kr),nr,Ir),tr,Pr),er,zr),rr,Er),g(g(g(g(g(g(g(g(g(g(fa,ir,Fr),or,Or),sr,Cr),fr,jr),lr,Nr),ur,Tr),cr,_r),mr,Lr),dr,$r),gr,Mr),g(g(g(g(fa,vr,Dr),pr,Rr),br,Wr),hr,Ur);var Yr="kit",Xr="kit-duotone",Hr="Kit",Gr="Kit Duotone";g(g({},Yr,Hr),Xr,Gr);var Vr={classic:{"fa-brands":"fab","fa-duotone":"fad","fa-light":"fal","fa-regular":"far","fa-solid":"fas","fa-thin":"fat"},duotone:{"fa-regular":"fadr","fa-light":"fadl","fa-thin":"fadt"},sharp:{"fa-solid":"fass","fa-regular":"fasr","fa-light":"fasl","fa-thin":"fast"},"sharp-duotone":{"fa-solid":"fasds","fa-regular":"fasdr","fa-light":"fasdl","fa-thin":"fasdt"},slab:{"fa-regular":"faslr"},"slab-press":{"fa-regular":"faslpr"},"slab-duo":{"fa-regular":"fasldr"},"slab-press-duo":{"fa-regular":"faslpdr"},pixel:{"fa-regular":"fapr"},mosaic:{"fa-solid":"fams"},vellum:{"fa-solid":"favs"},whiteboard:{"fa-semibold":"fawsb"},thumbprint:{"fa-light":"fatl"},notdog:{"fa-solid":"fans"},"notdog-duo":{"fa-solid":"fands"},etch:{"fa-solid":"faes"},graphite:{"fa-thin":"fagt"},jelly:{"fa-regular":"fajr"},"jelly-fill":{"fa-regular":"fajfr"},"jelly-duo":{"fa-regular":"fajdr"},chisel:{"fa-regular":"facr"},utility:{"fa-semibold":"fausb"},"utility-duo":{"fa-semibold":"faudsb"},"utility-fill":{"fa-semibold":"faufsb"}},Br={classic:["fas","far","fal","fat","fad"],duotone:["fadr","fadl","fadt"],sharp:["fass","fasr","fasl","fast"],"sharp-duotone":["fasds","fasdr","fasdl","fasdt"],slab:["faslr"],"slab-press":["faslpr"],"slab-duo":["fasldr"],"slab-press-duo":["faslpdr"],pixel:["fapr"],mosaic:["fams"],vellum:["favs"],whiteboard:["fawsb"],thumbprint:["fatl"],notdog:["fans"],"notdog-duo":["fands"],etch:["faes"],graphite:["fagt"],jelly:["fajr"],"jelly-fill":["fajfr"],"jelly-duo":["fajdr"],chisel:["facr"],utility:["fausb"],"utility-duo":["faudsb"],"utility-fill":["faufsb"]},Fa={classic:{fab:"fa-brands",fad:"fa-duotone",fal:"fa-light",far:"fa-regular",fas:"fa-solid",fat:"fa-thin"},duotone:{fadr:"fa-regular",fadl:"fa-light",fadt:"fa-thin"},sharp:{fass:"fa-solid",fasr:"fa-regular",fasl:"fa-light",fast:"fa-thin"},"sharp-duotone":{fasds:"fa-solid",fasdr:"fa-regular",fasdl:"fa-light",fasdt:"fa-thin"},slab:{faslr:"fa-regular"},"slab-press":{faslpr:"fa-regular"},"slab-duo":{fasldr:"fa-regular"},"slab-press-duo":{faslpdr:"fa-regular"},pixel:{fapr:"fa-regular"},mosaic:{fams:"fa-solid"},vellum:{favs:"fa-solid"},whiteboard:{fawsb:"fa-semibold"},thumbprint:{fatl:"fa-light"},notdog:{fans:"fa-solid"},"notdog-duo":{fands:"fa-solid"},etch:{faes:"fa-solid"},graphite:{fagt:"fa-thin"},jelly:{fajr:"fa-regular"},"jelly-fill":{fajfr:"fa-regular"},"jelly-duo":{fajdr:"fa-regular"},chisel:{facr:"fa-regular"},utility:{fausb:"fa-semibold"},"utility-duo":{faudsb:"fa-semibold"},"utility-fill":{faufsb:"fa-semibold"}},qr=["fa-solid","fa-regular","fa-light","fa-thin","fa-duotone","fa-brands","fa-semibold"],gt=["fa","fas","far","fal","fat","fad","fadr","fadl","fadt","fab","fass","fasr","fasl","fast","fasds","fasdr","fasdl","fasdt","faslr","faslpr","fasldr","faslpdr","fapr","fams","favs","fawsb","fatl","fans","fands","faes","fagt","fajr","fajfr","fajdr","facr","fausb","faudsb","faufsb"].concat(Be,qr),Kr=["solid","regular","light","thin","duotone","brands","semibold"],vt=[1,2,3,4,5,6,7,8,9,10],Jr=vt.concat([11,12,13,14,15,16,17,18,19,20]),Qr=["aw","fw","pull-left","pull-right"],Zr=[].concat(O(Object.keys(Br)),Kr,Qr,["2xs","xs","sm","lg","xl","2xl","beat","beat-fade","border","bounce","buzz","canvas-square","canvas-roomy","fade","flip-360","flip-both","flip-horizontal","flip-vertical","flip","float","inverse","jello","layers","layers-bottom-left","layers-bottom-right","layers-counter","layers-text","layers-top-left","layers-top-right","li","pull-end","pull-start","pulse","rotate-180","rotate-270","rotate-90","rotate-by","shake","spin-pulse","spin-reverse","spin","spin-snap","spin-snap-4","spin-snap-8","stack-1x","stack-2x","stack","swing","ul","wag","width-auto","width-fixed",la.GROUP,la.SWAP_OPACITY,la.PRIMARY,la.SECONDARY]).concat(vt.map(function(a){return"".concat(a,"x")})).concat(Jr.map(function(a){return"w-".concat(a)})),ai={"Font Awesome 5 Free":{900:"fas",400:"far"},"Font Awesome 5 Pro":{900:"fas",400:"far",normal:"far",300:"fal"},"Font Awesome 5 Brands":{400:"fab",normal:"fab"},"Font Awesome 5 Duotone":{900:"fad"}},N="___FONT_AWESOME___",Oa=16,pt="fa",bt="svg-inline--fa",W="data-fa-i2svg",Ca="data-fa-pseudo-element",ni="data-fa-pseudo-element-pending",Ga="data-prefix",Va="data-icon",cn="fontawesome-i2svg",ti="async",ei=["HTML","HEAD","STYLE","SCRIPT"],ht=["::before","::after",":before",":after"],yt=function(){try{return!0}catch{return!1}}();function ra(a){return new Proxy(a,{get:function(t,e){return e in t?t[e]:t[P]}})}var xt=l({},Un);xt[P]=l(l(l(l({},{"fa-duotone":"duotone"}),Un[P]),ln.kit),ln["kit-duotone"]);var ri=ra(xt),ja=l({},De);ja[P]=l(l(l(l({},{duotone:"fad"}),ja[P]),un.kit),un["kit-duotone"]);var mn=ra(ja),Na=l({},Fa);Na[P]=l(l({},Na[P]),Ve.kit);var Ba=ra(Na),Ta=l({},Vr);Ta[P]=l(l({},Ta[P]),He.kit);ra(Ta);var ii=le,St="fa-layers-text",oi=ue,si=l({},Le);ra(si);var fi=["class","data-prefix","data-icon","data-fa-transform","data-fa-mask"],Aa=ce,li=[].concat(O(Re),O(Zr)),Z=$.FontAwesomeConfig||{};function ui(a){var n=x.querySelector("script["+a+"]");if(n)return n.getAttribute(a)}function ci(a){return a===""?!0:a==="false"?!1:a==="true"?!0:a}if(x&&typeof x.querySelector=="function"){var mi=[["data-family-prefix","familyPrefix"],["data-css-prefix","cssPrefix"],["data-family-default","familyDefault"],["data-style-default","styleDefault"],["data-replacement-class","replacementClass"],["data-auto-replace-svg","autoReplaceSvg"],["data-auto-add-css","autoAddCss"],["data-search-pseudo-elements","searchPseudoElements"],["data-search-pseudo-elements-warnings","searchPseudoElementsWarnings"],["data-search-pseudo-elements-full-scan","searchPseudoElementsFullScan"],["data-observe-mutations","observeMutations"],["data-mutate-approach","mutateApproach"],["data-keep-original-source","keepOriginalSource"],["data-measure-performance","measurePerformance"],["data-show-missing-icons","showMissingIcons"]];mi.forEach(function(a){var n=pa(a,2),t=n[0],e=n[1],r=ci(ui(t));r!=null&&(Z[e]=r)})}var wt={styleDefault:"solid",familyDefault:P,cssPrefix:pt,replacementClass:bt,autoReplaceSvg:!0,autoAddCss:!0,searchPseudoElements:!1,searchPseudoElementsWarnings:!0,searchPseudoElementsFullScan:!1,observeMutations:!0,mutateApproach:"async",keepOriginalSource:!0,measurePerformance:!1,showMissingIcons:!0};Z.familyPrefix&&(Z.cssPrefix=Z.familyPrefix);var V=l(l({},wt),Z);V.autoReplaceSvg||(V.observeMutations=!1);var d={};Object.keys(wt).forEach(function(a){Object.defineProperty(d,a,{enumerable:!0,set:function(t){V[a]=t,aa.forEach(function(e){return e(d)})},get:function(){return V[a]}})});Object.defineProperty(d,"familyPrefix",{enumerable:!0,set:function(n){V.cssPrefix=n,aa.forEach(function(t){return t(d)})},get:function(){return V.cssPrefix}});$.FontAwesomeConfig=d;var aa=[];function di(a){return aa.push(a),function(){aa.splice(aa.indexOf(a),1)}}var Y=Oa,C={size:16,x:0,y:0,rotate:0,flipX:!1,flipY:!1};function gi(a){if(!(!a||!_)){var n=x.createElement("style");n.setAttribute("type","text/css"),n.innerHTML=a;for(var t=x.head.childNodes,e=null,r=t.length-1;r>-1;r--){var i=t[r],o=(i.tagName||"").toUpperCase();["STYLE","LINK"].indexOf(o)>-1&&(e=i)}return x.head.insertBefore(n,e),a}}var vi="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";function dn(){for(var a=12,n="";a-- >0;)n+=vi[Math.random()*62|0];return n}function q(a){for(var n=[],t=(a||[]).length>>>0;t--;)n[t]=a[t];return n}function qa(a){return a.classList?q(a.classList):(a.getAttribute("class")||"").split(" ").filter(function(n){return n})}function At(a){return"".concat(a).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/'/g,"&#39;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}function pi(a){return Object.keys(a||{}).reduce(function(n,t){return n+"".concat(t,'="').concat(At(a[t]),'" ')},"").trim()}function ba(a){return Object.keys(a||{}).reduce(function(n,t){return n+"".concat(t,": ").concat(a[t].trim(),";")},"")}function Ka(a){return a.size!==C.size||a.x!==C.x||a.y!==C.y||a.rotate!==C.rotate||a.flipX||a.flipY}function bi(a){var n=a.transform,t=a.containerWidth,e=a.iconWidth,r={transform:"translate(".concat(t/2," 256)")},i="translate(".concat(n.x*32,", ").concat(n.y*32,") "),o="scale(".concat(n.size/16*(n.flipX?-1:1),", ").concat(n.size/16*(n.flipY?-1:1),") "),s="rotate(".concat(n.rotate," 0 0)"),f={transform:"".concat(i," ").concat(o," ").concat(s)},u={transform:"translate(".concat(e/2*-1," -256)")};return{outer:r,inner:f,path:u}}function hi(a){var n=a.transform,t=a.width,e=t===void 0?Oa:t,r=a.height,i=r===void 0?Oa:r,o="";return Wn?o+="translate(".concat(n.x/Y-e/2,"em, ").concat(n.y/Y-i/2,"em) "):o+="translate(calc(-50% + ".concat(n.x/Y,"em), calc(-50% + ").concat(n.y/Y,"em)) "),o+="scale(".concat(n.size/Y*(n.flipX?-1:1),", ").concat(n.size/Y*(n.flipY?-1:1),") "),o+="rotate(".concat(n.rotate,"deg) "),o}var yi=`:root, :host {
  --fa-font-solid: normal 900 1em/1 'Font Awesome 7 Free';
  --fa-font-regular: normal 400 1em/1 'Font Awesome 7 Free';
  --fa-font-light: normal 300 1em/1 'Font Awesome 7 Pro';
  --fa-font-thin: normal 100 1em/1 'Font Awesome 7 Pro';
  --fa-font-duotone: normal 900 1em/1 'Font Awesome 7 Duotone';
  --fa-font-duotone-regular: normal 400 1em/1 'Font Awesome 7 Duotone';
  --fa-font-duotone-light: normal 300 1em/1 'Font Awesome 7 Duotone';
  --fa-font-duotone-thin: normal 100 1em/1 'Font Awesome 7 Duotone';
  --fa-font-brands: normal 400 1em/1 'Font Awesome 7 Brands';
  --fa-font-sharp-solid: normal 900 1em/1 'Font Awesome 7 Sharp';
  --fa-font-sharp-regular: normal 400 1em/1 'Font Awesome 7 Sharp';
  --fa-font-sharp-light: normal 300 1em/1 'Font Awesome 7 Sharp';
  --fa-font-sharp-thin: normal 100 1em/1 'Font Awesome 7 Sharp';
  --fa-font-sharp-duotone-solid: normal 900 1em/1 'Font Awesome 7 Sharp Duotone';
  --fa-font-sharp-duotone-regular: normal 400 1em/1 'Font Awesome 7 Sharp Duotone';
  --fa-font-sharp-duotone-light: normal 300 1em/1 'Font Awesome 7 Sharp Duotone';
  --fa-font-sharp-duotone-thin: normal 100 1em/1 'Font Awesome 7 Sharp Duotone';
  --fa-font-slab-regular: normal 400 1em/1 'Font Awesome 7 Slab';
  --fa-font-slab-press-regular: normal 400 1em/1 'Font Awesome 7 Slab Press';
  --fa-font-slab-duo-regular: normal 400 1em/1 'Font Awesome 7 Slab Duo';
  --fa-font-slab-press-duo-regular: normal 400 1em/1 'Font Awesome 7 Slab Press Duo';
  --fa-font-pixel-regular: normal 400 1em/1 'Font Awesome 7 Pixel';
  --fa-font-mosaic-solid: normal 900 1em/1 'Font Awesome 7 Mosaic';
  --fa-font-vellum-solid: normal 900 1em/1 'Font Awesome 7 Vellum';
  --fa-font-whiteboard-semibold: normal 600 1em/1 'Font Awesome 7 Whiteboard';
  --fa-font-thumbprint-light: normal 300 1em/1 'Font Awesome 7 Thumbprint';
  --fa-font-notdog-solid: normal 900 1em/1 'Font Awesome 7 Notdog';
  --fa-font-notdog-duo-solid: normal 900 1em/1 'Font Awesome 7 Notdog Duo';
  --fa-font-etch-solid: normal 900 1em/1 'Font Awesome 7 Etch';
  --fa-font-graphite-thin: normal 100 1em/1 'Font Awesome 7 Graphite';
  --fa-font-jelly-regular: normal 400 1em/1 'Font Awesome 7 Jelly';
  --fa-font-jelly-fill-regular: normal 400 1em/1 'Font Awesome 7 Jelly Fill';
  --fa-font-jelly-duo-regular: normal 400 1em/1 'Font Awesome 7 Jelly Duo';
  --fa-font-chisel-regular: normal 400 1em/1 'Font Awesome 7 Chisel';
  --fa-font-utility-semibold: normal 600 1em/1 'Font Awesome 7 Utility';
  --fa-font-utility-duo-semibold: normal 600 1em/1 'Font Awesome 7 Utility Duo';
  --fa-font-utility-fill-semibold: normal 600 1em/1 'Font Awesome 7 Utility Fill';
}

.svg-inline--fa {
  box-sizing: content-box;
  display: var(--fa-display, inline-block);
  height: 1em;
  overflow: visible;
  vertical-align: -0.125em;
  width: var(--fa-width, 1.25em);
}
.svg-inline--fa.fa-2xs {
  vertical-align: 0.1em;
}
.svg-inline--fa.fa-xs {
  vertical-align: 0em;
}
.svg-inline--fa.fa-sm {
  vertical-align: -0.0714285714em;
}
.svg-inline--fa.fa-lg {
  vertical-align: -0.2em;
}
.svg-inline--fa.fa-xl {
  vertical-align: -0.25em;
}
.svg-inline--fa.fa-2xl {
  vertical-align: -0.3125em;
}
.svg-inline--fa.fa-pull-left,
.svg-inline--fa .fa-pull-start {
  float: inline-start;
  margin-inline-end: var(--fa-pull-margin, 0.3em);
}
.svg-inline--fa.fa-pull-right,
.svg-inline--fa .fa-pull-end {
  float: inline-end;
  margin-inline-start: var(--fa-pull-margin, 0.3em);
}
.svg-inline--fa.fa-li {
  width: var(--fa-li-width, 2em);
  inset-inline-start: calc(-1 * var(--fa-li-width, 2em));
  inset-block-start: 0.25em; /* syncing vertical alignment with Web Font rendering */
}

.fa-layers-counter, .fa-layers-text {
  display: inline-block;
  position: absolute;
  text-align: center;
}

.fa-layers {
  display: inline-block;
  height: 1em;
  position: relative;
  text-align: center;
  vertical-align: -0.125em;
  width: var(--fa-width, 1.25em);
}
.fa-layers .svg-inline--fa {
  inset: 0;
  margin: auto;
  position: absolute;
  transform-origin: center center;
}

.fa-layers-text {
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  transform-origin: center center;
}

.fa-layers-counter {
  background-color: var(--fa-counter-background-color, #ff253a);
  border-radius: var(--fa-counter-border-radius, 1em);
  box-sizing: border-box;
  color: var(--fa-inverse, #fff);
  line-height: var(--fa-counter-line-height, 1);
  max-width: var(--fa-counter-max-width, 5em);
  min-width: var(--fa-counter-min-width, 1.5em);
  overflow: hidden;
  padding: var(--fa-counter-padding, 0.25em 0.5em);
  right: var(--fa-right, 0);
  text-overflow: ellipsis;
  top: var(--fa-top, 0);
  transform: scale(var(--fa-counter-scale, 0.25));
  transform-origin: top right;
}

.fa-layers-bottom-right {
  bottom: var(--fa-bottom, 0);
  right: var(--fa-right, 0);
  top: auto;
  transform: scale(var(--fa-layers-scale, 0.25));
  transform-origin: bottom right;
}

.fa-layers-bottom-left {
  bottom: var(--fa-bottom, 0);
  left: var(--fa-left, 0);
  right: auto;
  top: auto;
  transform: scale(var(--fa-layers-scale, 0.25));
  transform-origin: bottom left;
}

.fa-layers-top-right {
  top: var(--fa-top, 0);
  right: var(--fa-right, 0);
  transform: scale(var(--fa-layers-scale, 0.25));
  transform-origin: top right;
}

.fa-layers-top-left {
  left: var(--fa-left, 0);
  right: auto;
  top: var(--fa-top, 0);
  transform: scale(var(--fa-layers-scale, 0.25));
  transform-origin: top left;
}

.fa-1x {
  font-size: 1em;
}

.fa-2x {
  font-size: 2em;
}

.fa-3x {
  font-size: 3em;
}

.fa-4x {
  font-size: 4em;
}

.fa-5x {
  font-size: 5em;
}

.fa-6x {
  font-size: 6em;
}

.fa-7x {
  font-size: 7em;
}

.fa-8x {
  font-size: 8em;
}

.fa-9x {
  font-size: 9em;
}

.fa-10x {
  font-size: 10em;
}

.fa-2xs {
  font-size: calc(10 / 16 * 1em); /* converts a 10px size into an em-based value that's relative to the scale's 16px base */
  line-height: calc(1 / 10 * 1em); /* sets the line-height of the icon back to that of it's parent */
  vertical-align: calc((6 / 10 - 0.375) * 1em); /* vertically centers the icon taking into account the surrounding text's descender */
}

.fa-xs {
  font-size: calc(12 / 16 * 1em); /* converts a 12px size into an em-based value that's relative to the scale's 16px base */
  line-height: calc(1 / 12 * 1em); /* sets the line-height of the icon back to that of it's parent */
  vertical-align: calc((6 / 12 - 0.375) * 1em); /* vertically centers the icon taking into account the surrounding text's descender */
}

.fa-sm {
  font-size: calc(14 / 16 * 1em); /* converts a 14px size into an em-based value that's relative to the scale's 16px base */
  line-height: calc(1 / 14 * 1em); /* sets the line-height of the icon back to that of it's parent */
  vertical-align: calc((6 / 14 - 0.375) * 1em); /* vertically centers the icon taking into account the surrounding text's descender */
}

.fa-lg {
  font-size: calc(20 / 16 * 1em); /* converts a 20px size into an em-based value that's relative to the scale's 16px base */
  line-height: calc(1 / 20 * 1em); /* sets the line-height of the icon back to that of it's parent */
  vertical-align: calc((6 / 20 - 0.375) * 1em); /* vertically centers the icon taking into account the surrounding text's descender */
}

.fa-xl {
  font-size: calc(24 / 16 * 1em); /* converts a 24px size into an em-based value that's relative to the scale's 16px base */
  line-height: calc(1 / 24 * 1em); /* sets the line-height of the icon back to that of it's parent */
  vertical-align: calc((6 / 24 - 0.375) * 1em); /* vertically centers the icon taking into account the surrounding text's descender */
}

.fa-2xl {
  font-size: calc(32 / 16 * 1em); /* converts a 32px size into an em-based value that's relative to the scale's 16px base */
  line-height: calc(1 / 32 * 1em); /* sets the line-height of the icon back to that of it's parent */
  vertical-align: calc((6 / 32 - 0.375) * 1em); /* vertically centers the icon taking into account the surrounding text's descender */
}

.fa-width-auto {
  --fa-width: auto;
}

.fa-fw,
.fa-width-fixed {
  --fa-width: 1.25em;
}

.fa-canvas-square {
  padding-block: 0.125em;
  margin-block-end: -0.125em;
}

.fa-canvas-roomy {
  padding-block: 0.25em;
  padding-inline: 0.125em;
  margin-block-end: -0.25em;
  box-sizing: content-box;
}

.fa-ul {
  list-style-type: none;
  margin-inline-start: var(--fa-li-margin, 2.5em);
  padding-inline-start: 0;
}
.fa-ul > li {
  position: relative;
}

.fa-li {
  inset-inline-start: calc(-1 * var(--fa-li-width, 2em));
  position: absolute;
  text-align: center;
  width: var(--fa-li-width, 2em);
  line-height: inherit;
}

/* Heads Up: Bordered Icons will not be supported in the future!
  - This feature will be deprecated in the next major release of Font Awesome (v8)!
  - You may continue to use it in this version *v7), but it will not be supported in Font Awesome v8.
*/
/* Notes:
* --@{v.$css-prefix}-border-width = 1/16 by default (to render as ~1px based on a 16px default font-size)
* --@{v.$css-prefix}-border-padding =
  ** 3/16 for vertical padding (to give ~2px of vertical whitespace around an icon considering it's vertical alignment)
  ** 4/16 for horizontal padding (to give ~4px of horizontal whitespace around an icon)
*/
.fa-border {
  border-color: var(--fa-border-color, #eee);
  border-radius: var(--fa-border-radius, 0.1em);
  border-style: var(--fa-border-style, solid);
  border-width: var(--fa-border-width, 0.0625em);
  box-sizing: var(--fa-border-box-sizing, content-box);
  padding: var(--fa-border-padding, 0.1875em 0.25em);
}

.fa-pull-left,
.fa-pull-start {
  float: inline-start;
  margin-inline-end: var(--fa-pull-margin, 0.3em);
}

.fa-pull-right,
.fa-pull-end {
  float: inline-end;
  margin-inline-start: var(--fa-pull-margin, 0.3em);
}

.fa-beat {
  animation-name: fa-beat;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
}

.fa-bounce {
  animation-name: fa-bounce;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, cubic-bezier(0.28, 0.84, 0.42, 1));
}

.fa-fade {
  animation-name: fa-fade;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
}

.fa-beat-fade {
  animation-name: fa-beat-fade;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
}

.fa-flip {
  animation-name: fa-flip;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1.5s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
}

.fa-flip-360 {
  animation-name: fa-flip-360;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
}

.fa-shake {
  animation-name: fa-shake;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 0.75s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
}

.fa-spin {
  animation-name: fa-spin;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 2s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, linear);
}

.fa-spin-reverse {
  --fa-animation-direction: reverse;
}

.fa-pulse,
.fa-spin-pulse {
  animation-name: fa-spin;
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, steps(8));
}

.fa-spin-snap {
  animation-name: fa-spin-snap;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 3s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, linear);
}

.fa-spin-snap-4 {
  animation-name: fa-spin-snap-4;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 2.4s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, linear);
}

.fa-spin-snap-8 {
  animation-name: fa-spin-snap-8;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 4s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, linear);
}

.fa-buzz {
  animation-name: fa-buzz;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 0.6s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, linear);
}

.fa-wag {
  animation-name: fa-wag;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 0.9s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-out);
  transform-origin: bottom center;
}

.fa-float {
  animation-name: fa-float;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 3s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-in-out);
  will-change: transform;
}

.fa-swing {
  animation-name: fa-swing;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 1.2s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-out);
  transform-origin: top center;
}

.fa-jello {
  animation-name: fa-jello;
  animation-delay: var(--fa-animation-delay, 0s);
  animation-direction: var(--fa-animation-direction, normal);
  animation-duration: var(--fa-animation-duration, 0.9s);
  animation-iteration-count: var(--fa-animation-iteration-count, infinite);
  animation-timing-function: var(--fa-animation-timing, ease-out);
}

@media (prefers-reduced-motion: reduce) {
  .fa-beat,
  .fa-bounce,
  .fa-fade,
  .fa-beat-fade,
  .fa-flip,
  .fa-flip-360,
  .fa-pulse,
  .fa-shake,
  .fa-spin,
  .fa-spin-pulse,
  .fa-buzz,
  .fa-float,
  .fa-jello,
  .fa-spin-snap,
  .fa-spin-snap-4,
  .fa-spin-snap-8,
  .fa-swing,
  .fa-wag {
    animation: none !important;
    transition: none !important;
  }
}
@keyframes fa-beat {
  0% {
    transform: scale(1);
  }
  25% {
    transform: scale(calc(1.25 * var(--fa-beat-scale, 1.25)));
  }
  45% {
    transform: scale(calc(1.22 * var(--fa-beat-scale, 1.22)));
  }
  65% {
    transform: scale(calc(1.25 * var(--fa-beat-scale, 1.25)));
  }
  90% {
    transform: scale(1);
  }
}
@keyframes fa-bounce {
  0% {
    transform: scale(1, 1) translateY(0);
    animation-timing-function: var(--fa-animation-timing);
  }
  14% {
    transform: scale(var(--fa-bounce-start-scale-x, 1.06), var(--fa-bounce-start-scale-y, 0.94)) translateY(var(--fa-bounce-anticipation, 3px));
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
  }
  32% {
    transform: scale(var(--fa-bounce-jump-scale-x, 0.94), var(--fa-bounce-jump-scale-y, 1.12)) translateY(calc(-1 * var(--fa-bounce-height, 0.5em)));
    animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
  }
  52% {
    transform: scale(1, 1) translateY(calc(-1 * var(--fa-bounce-height, 0.5em) * 1.1));
    animation-timing-function: cubic-bezier(0.5, 0, 1, 0.5);
  }
  70% {
    transform: scale(var(--fa-bounce-land-scale-x, 1.06), var(--fa-bounce-land-scale-y, 0.92)) translateY(0);
    animation-timing-function: cubic-bezier(0.33, 0.33, 0.66, 1);
  }
  85% {
    transform: scale(0.98, 1.04) translateY(calc(-2px * var(--fa-bounce-rebound, 1)));
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
  }
  100% {
    transform: scale(1, 1) translateY(0);
  }
}
@keyframes fa-fade {
  0% {
    opacity: 1;
    transform: scale(1);
    animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
  }
  40% {
    opacity: var(--fa-fade-opacity, 0.4);
    transform: scale(0.98);
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  100% {
    opacity: 1;
    transform: scale(1);
  }
}
@keyframes fa-beat-fade {
  0% {
    opacity: var(--fa-beat-fade-opacity, 0.4);
    transform: scale(1);
    animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
  }
  25% {
    opacity: calc(var(--fa-beat-fade-opacity, 0.4) + 0.4);
    transform: scale(var(--fa-beat-fade-scale, 1.28));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  45% {
    opacity: 1;
    transform: scale(var(--fa-beat-fade-scale, 1.25));
    animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  }
  65% {
    opacity: calc(var(--fa-beat-fade-opacity, 0.4) + 0.4);
    transform: scale(var(--fa-beat-fade-scale, 1.28));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  100% {
    opacity: var(--fa-beat-fade-opacity, 0.4);
    transform: scale(1);
  }
}
@keyframes fa-flip {
  0% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), 0deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
  }
  8% {
    transform: perspective(2em) scale(var(--fa-flip-anticipation-scale, 0.95)) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), 0deg);
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
  }
  35% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), calc(var(--fa-flip-angle, -360deg) * 0.6));
    animation-timing-function: linear;
  }
  65% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), calc(var(--fa-flip-angle, -360deg) * 0.5));
    animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
  }
  92% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), calc(var(--fa-flip-angle, -360deg) * var(--fa-flip-overshoot, 1.04)));
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
  }
  100% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), var(--fa-flip-angle, -360deg));
  }
}
@keyframes fa-flip-360 {
  0% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), 0deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
  }
  8% {
    transform: perspective(2em) scale(var(--fa-flip-anticipation-scale, 0.95)) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), 0deg);
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
  }
  50% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), calc(var(--fa-flip-angle, -360deg) * 0.6));
    animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
  }
  80% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), calc(var(--fa-flip-angle, -360deg) * var(--fa-flip-overshoot, 1.04)));
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
  }
  100% {
    transform: perspective(2em) scale(1) rotate3d(var(--fa-flip-x, 0), var(--fa-flip-y, 1), var(--fa-flip-z, 0), var(--fa-flip-angle, -360deg));
  }
}
@keyframes fa-shake {
  0% {
    transform: rotate(0deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.8, 1);
  }
  8% {
    transform: rotate(35deg) translateX(1px);
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  20% {
    transform: rotate(-22deg) translateX(-1px);
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  35% {
    transform: rotate(15deg) translateX(1px);
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  50% {
    transform: rotate(-9deg);
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  65% {
    transform: rotate(5deg);
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  78% {
    transform: rotate(-3deg);
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  90% {
    transform: rotate(1deg);
    animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  }
  100% {
    transform: rotate(0deg);
  }
}
@keyframes fa-spin {
  0% {
    transform: rotate(0deg);
  }
  100% {
    transform: rotate(360deg);
  }
}
@keyframes fa-spin-snap {
  0% {
    transform: rotate(0deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  12% {
    transform: rotate(60deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  16.67% {
    transform: rotate(60deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  28.67% {
    transform: rotate(120deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  33.33% {
    transform: rotate(120deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  45.33% {
    transform: rotate(180deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  50% {
    transform: rotate(180deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  62% {
    transform: rotate(240deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  66.67% {
    transform: rotate(240deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  78.67% {
    transform: rotate(300deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  83.33% {
    transform: rotate(300deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  95.33% {
    transform: rotate(360deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  100% {
    transform: rotate(360deg);
  }
}
@keyframes fa-spin-snap-4 {
  0% {
    transform: rotate(0deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  15% {
    transform: rotate(90deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  25% {
    transform: rotate(90deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  40% {
    transform: rotate(180deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  50% {
    transform: rotate(180deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  65% {
    transform: rotate(270deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  75% {
    transform: rotate(270deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  90% {
    transform: rotate(360deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  100% {
    transform: rotate(360deg);
  }
}
@keyframes fa-spin-snap-8 {
  0% {
    transform: rotate(0deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  9% {
    transform: rotate(45deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  12.5% {
    transform: rotate(45deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  21.5% {
    transform: rotate(90deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  25% {
    transform: rotate(90deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  34% {
    transform: rotate(135deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  37.5% {
    transform: rotate(135deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  46.5% {
    transform: rotate(180deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  50% {
    transform: rotate(180deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  59% {
    transform: rotate(225deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  62.5% {
    transform: rotate(225deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  71.5% {
    transform: rotate(270deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  75% {
    transform: rotate(270deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  84% {
    transform: rotate(315deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  87.5% {
    transform: rotate(315deg);
    animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
  }
  96.5% {
    transform: rotate(360deg);
    animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
  }
  100% {
    transform: rotate(360deg);
  }
}
@keyframes fa-buzz {
  0% {
    transform: translateX(0) rotate(0deg);
    animation-timing-function: cubic-bezier(0.1, 0, 0.9, 1);
  }
  5% {
    transform: translateX(var(--fa-buzz-distance, 4px)) rotate(0.5deg);
  }
  10% {
    transform: translateX(calc(-1 * var(--fa-buzz-distance, 4px))) rotate(-0.5deg);
  }
  15% {
    transform: translateX(var(--fa-buzz-distance, 4px)) rotate(0.3deg);
  }
  20% {
    transform: translateX(calc(-1 * var(--fa-buzz-distance, 4px))) rotate(-0.3deg);
  }
  25% {
    transform: translateX(calc(var(--fa-buzz-distance, 4px) * 0.7)) rotate(0.2deg);
  }
  30% {
    transform: translateX(calc(-1 * var(--fa-buzz-distance, 4px) * 0.7)) rotate(-0.2deg);
  }
  35% {
    transform: translateX(calc(var(--fa-buzz-distance, 4px) * 0.4)) rotate(0.1deg);
  }
  40% {
    transform: translateX(0) rotate(0deg);
  }
  100% {
    transform: translateX(0) rotate(0deg);
  }
}
@keyframes fa-wag {
  0% {
    transform: rotate(0deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.6, 1);
  }
  12% {
    transform: rotate(var(--fa-wag-angle, 12deg));
    animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  }
  24% {
    transform: rotate(2deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.6, 1);
  }
  36% {
    transform: rotate(calc(var(--fa-wag-angle, 12deg) * 0.85));
    animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  }
  48% {
    transform: rotate(1deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.6, 1);
  }
  58% {
    transform: rotate(calc(var(--fa-wag-angle, 12deg) * 0.6));
    animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  }
  68% {
    transform: rotate(0deg);
  }
  100% {
    transform: rotate(0deg);
  }
}
@keyframes fa-float {
  0% {
    transform: translateY(0) translateX(0) rotate(0deg) scale(var(--fa-float-squash-x, 1.02), var(--fa-float-squash-y, 0.98));
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
  }
  15% {
    transform: translateY(calc(-0.4 * var(--fa-float-height, 6px))) translateX(var(--fa-float-drift, 1px)) rotate(var(--fa-float-tilt, 1deg)) scale(1, 1);
    animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
  }
  35% {
    transform: translateY(calc(-1 * var(--fa-float-height, 6px))) translateX(0) rotate(0deg) scale(var(--fa-float-stretch-x, 0.98), var(--fa-float-stretch-y, 1.03));
    animation-timing-function: cubic-bezier(0.5, 0, 0.5, 0);
  }
  50% {
    transform: translateY(calc(-0.92 * var(--fa-float-height, 6px))) translateX(calc(-0.5 * var(--fa-float-drift, 1px))) rotate(calc(-0.5 * var(--fa-float-tilt, 1deg))) scale(0.995, 1.01);
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
  }
  70% {
    transform: translateY(calc(-0.3 * var(--fa-float-height, 6px))) translateX(calc(-1 * var(--fa-float-drift, 1px))) rotate(calc(-1 * var(--fa-float-tilt, 1deg))) scale(1, 1);
    animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
  }
  90% {
    transform: translateY(calc(0.05 * var(--fa-float-height, 6px))) translateX(0) rotate(0deg) scale(var(--fa-float-squash-x, 1.02), var(--fa-float-squash-y, 0.98));
    animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
  }
  100% {
    transform: translateY(0) translateX(0) rotate(0deg) scale(var(--fa-float-squash-x, 1.02), var(--fa-float-squash-y, 0.98));
  }
}
@keyframes fa-swing {
  0% {
    transform: rotate(0deg);
    animation-timing-function: cubic-bezier(0.2, 0, 0.8, 1);
  }
  8% {
    transform: rotate(var(--fa-swing-angle, 22deg));
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  18% {
    transform: rotate(calc(-1 * var(--fa-swing-angle, 22deg) * 0.85));
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  28% {
    transform: rotate(calc(var(--fa-swing-angle, 22deg) * 0.65));
    animation-timing-function: cubic-bezier(0.35, 0, 0.65, 1);
  }
  38% {
    transform: rotate(calc(-1 * var(--fa-swing-angle, 22deg) * 0.45));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  48% {
    transform: rotate(calc(var(--fa-swing-angle, 22deg) * 0.25));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  56% {
    transform: rotate(calc(-1 * var(--fa-swing-angle, 22deg) * 0.1));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  64% {
    transform: rotate(0deg);
  }
  100% {
    transform: rotate(0deg);
  }
}
@keyframes fa-jello {
  0% {
    transform: scale(1, 1);
    animation-timing-function: cubic-bezier(0.2, 0, 0.8, 1);
  }
  12% {
    transform: scale(var(--fa-jello-scale-x, 1.15), calc(2 - var(--fa-jello-scale-x, 1.15)));
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  24% {
    transform: scale(calc(2 - var(--fa-jello-scale-y, 1.12)), var(--fa-jello-scale-y, 1.12));
    animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
  }
  36% {
    transform: scale(calc(1 + (var(--fa-jello-scale-x, 1.15) - 1) * 0.5), calc(2 - (1 + (var(--fa-jello-scale-x, 1.15) - 1) * 0.5)));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  48% {
    transform: scale(calc(2 - (1 + (var(--fa-jello-scale-y, 1.12) - 1) * 0.3)), calc(1 + (var(--fa-jello-scale-y, 1.12) - 1) * 0.3));
    animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
  }
  58% {
    transform: scale(1.02, 0.98);
    animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
  }
  68% {
    transform: scale(1, 1);
  }
  100% {
    transform: scale(1, 1);
  }
}
.fa-rotate-90 {
  transform: rotate(90deg);
}

.fa-rotate-180 {
  transform: rotate(180deg);
}

.fa-rotate-270 {
  transform: rotate(270deg);
}

.fa-flip-horizontal {
  transform: scale(-1, 1);
}

.fa-flip-vertical {
  transform: scale(1, -1);
}

.fa-flip-both,
.fa-flip-horizontal.fa-flip-vertical {
  transform: scale(-1, -1);
}

.fa-rotate-by {
  transform: rotate(var(--fa-rotate-angle, 0));
}

.svg-inline--fa .fa-primary {
  fill: var(--fa-primary-color, currentColor);
  opacity: var(--fa-primary-opacity, 1);
}

.svg-inline--fa .fa-secondary {
  fill: var(--fa-secondary-color, currentColor);
  opacity: var(--fa-secondary-opacity, 0.4);
}

.svg-inline--fa.fa-swap-opacity .fa-primary {
  opacity: var(--fa-secondary-opacity, 0.4);
}

.svg-inline--fa.fa-swap-opacity .fa-secondary {
  opacity: var(--fa-primary-opacity, 1);
}

.svg-inline--fa mask .fa-primary,
.svg-inline--fa mask .fa-secondary {
  fill: black;
}

.svg-inline--fa.fa-inverse {
  fill: var(--fa-inverse, #fff);
}

.fa-stack {
  display: inline-block;
  height: 2em;
  line-height: 2em;
  position: relative;
  vertical-align: middle;
  width: 2.5em;
}

.fa-inverse {
  color: var(--fa-inverse, #fff);
}

.svg-inline--fa.fa-stack-1x {
  --fa-width: 1.25em;
  height: 1em;
  width: var(--fa-width);
}
.svg-inline--fa.fa-stack-2x {
  --fa-width: 2.5em;
  height: 2em;
  width: var(--fa-width);
}

.fa-stack-1x,
.fa-stack-2x {
  inset: 0;
  margin: auto;
  position: absolute;
  z-index: var(--fa-stack-z-index, auto);
}`;function kt(){var a=pt,n=bt,t=d.cssPrefix,e=d.replacementClass,r=yi;if(t!==a||e!==n){var i=new RegExp("\\.".concat(a,"\\-"),"g"),o=new RegExp("\\--".concat(a,"\\-"),"g"),s=new RegExp("\\.".concat(n),"g");r=r.replace(i,".".concat(t,"-")).replace(o,"--".concat(t,"-")).replace(s,".".concat(e))}return r}var gn=!1;function ka(){d.autoAddCss&&!gn&&(gi(kt()),gn=!0)}var xi={mixout:function(){return{dom:{css:kt,insertCss:ka}}},hooks:function(){return{beforeDOMElementCreation:function(){ka()},beforeI2svg:function(){ka()}}}},T=$||{};T[N]||(T[N]={});T[N].styles||(T[N].styles={});T[N].hooks||(T[N].hooks={});T[N].shims||(T[N].shims=[]);var F=T[N],It=[],Pt=function(){x.removeEventListener("DOMContentLoaded",Pt),ga=1,It.map(function(n){return n()})},ga=!1;_&&(ga=(x.documentElement.doScroll?/^loaded|^c/:/^loaded|^i|^c/).test(x.readyState),ga||x.addEventListener("DOMContentLoaded",Pt));function Si(a){_&&(ga?setTimeout(a,0):It.push(a))}function ia(a){var n=a.tag,t=a.attributes,e=t===void 0?{}:t,r=a.children,i=r===void 0?[]:r;return typeof a=="string"?At(a):"<".concat(n," ").concat(pi(e),">").concat(i.map(ia).join(""),"</").concat(n,">")}function vn(a,n,t){if(a&&a[n]&&a[n][t])return{prefix:n,iconName:t,icon:a[n][t]}}var Ia=function(n,t,e,r){var i=Object.keys(n),o=i.length,s=t,f,u,m;for(e===void 0?(f=1,m=n[i[0]]):(f=0,m=e);f<o;f++)u=i[f],m=s(m,n[u],u,n);return m};function zt(a){return O(a).length!==1?null:a.codePointAt(0).toString(16)}function pn(a){return Object.keys(a).reduce(function(n,t){var e=a[t],r=!!e.icon;return r?n[e.iconName]=e.icon:n[t]=e,n},{})}function _a(a,n){var t=arguments.length>2&&arguments[2]!==void 0?arguments[2]:{},e=t.skipHooks,r=e===void 0?!1:e,i=pn(n);typeof F.hooks.addPack=="function"&&!r?F.hooks.addPack(a,pn(n)):F.styles[a]=l(l({},F.styles[a]||{}),i),a==="fas"&&_a("fa",n)}var ta=F.styles,wi=F.shims,Et=Object.keys(Ba),Ai=Et.reduce(function(a,n){return a[n]=Object.keys(Ba[n]),a},{}),Ja=null,Ft={},Ot={},Ct={},jt={},Nt={};function ki(a){return~li.indexOf(a)}function Ii(a,n){var t=n.split("-"),e=t[0],r=t.slice(1).join("-");return e===a&&r!==""&&!ki(r)?r:null}var Tt=function(){var n=function(i){return Ia(ta,function(o,s,f){return o[f]=Ia(s,i,{}),o},{})};Ft=n(function(r,i,o){if(i[3]&&(r[i[3]]=o),i[2]){var s=i[2].filter(function(f){return typeof f=="number"});s.forEach(function(f){r[f.toString(16)]=o})}return r}),Ot=n(function(r,i,o){if(r[o]=o,i[2]){var s=i[2].filter(function(f){return typeof f=="string"});s.forEach(function(f){r[f]=o})}return r}),Nt=n(function(r,i,o){var s=i[2];return r[o]=o,s.forEach(function(f){r[f]=o}),r});var t="far"in ta||d.autoFetchSvg,e=Ia(wi,function(r,i){var o=i[0],s=i[1],f=i[2];return s==="far"&&!t&&(s="fas"),typeof o=="string"&&(r.names[o]={prefix:s,iconName:f}),typeof o=="number"&&(r.unicodes[o.toString(16)]={prefix:s,iconName:f}),r},{names:{},unicodes:{}});Ct=e.names,jt=e.unicodes,Ja=ha(d.styleDefault,{family:d.familyDefault})};di(function(a){Ja=ha(a.styleDefault,{family:d.familyDefault})});Tt();function Qa(a,n){return(Ft[a]||{})[n]}function Pi(a,n){return(Ot[a]||{})[n]}function R(a,n){return(Nt[a]||{})[n]}function _t(a){return Ct[a]||{prefix:null,iconName:null}}function zi(a){var n=jt[a],t=Qa("fas",a);return n||(t?{prefix:"fas",iconName:t}:null)||{prefix:null,iconName:null}}function M(){return Ja}var Lt=function(){return{prefix:null,iconName:null,rest:[]}};function Ei(a){var n=P,t=Et.reduce(function(e,r){return e[r]="".concat(d.cssPrefix,"-").concat(r),e},{});return mt.forEach(function(e){(a.includes(t[e])||a.some(function(r){return Ai[e].includes(r)}))&&(n=e)}),n}function ha(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},t=n.family,e=t===void 0?P:t,r=ri[e][a];if(e===ea&&!a)return"fad";var i=mn[e][a]||mn[e][r],o=a in F.styles?a:null,s=i||o||null;return s}function Fi(a){var n=[],t=null;return a.forEach(function(e){var r=Ii(d.cssPrefix,e);r?t=r:e&&n.push(e)}),{iconName:t,rest:n}}function bn(a){return a.sort().filter(function(n,t,e){return e.indexOf(n)===t})}var hn=gt.concat(dt);function ya(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},t=n.skipLookups,e=t===void 0?!1:t,r=null,i=bn(a.filter(function(p){return hn.includes(p)})),o=bn(a.filter(function(p){return!hn.includes(p)})),s=i.filter(function(p){return r=p,!Yn.includes(p)}),f=pa(s,1),u=f[0],m=u===void 0?null:u,c=Ei(i),v=l(l({},Fi(o)),{},{prefix:ha(m,{family:c})});return l(l(l({},v),Ni({values:a,family:c,styles:ta,config:d,canonical:v,givenPrefix:r})),Oi(e,r,v))}function Oi(a,n,t){var e=t.prefix,r=t.iconName;if(a||!e||!r)return{prefix:e,iconName:r};var i=n==="fa"?_t(r):{},o=R(e,r);return r=i.iconName||o||r,e=i.prefix||e,e==="far"&&!ta.far&&ta.fas&&!d.autoFetchSvg&&(e="fas"),{prefix:e,iconName:r}}var Ci=mt.filter(function(a){return a!==P||a!==ea}),ji=Object.keys(Fa).filter(function(a){return a!==P}).map(function(a){return Object.keys(Fa[a])}).flat();function Ni(a){var n=a.values,t=a.family,e=a.canonical,r=a.givenPrefix,i=r===void 0?"":r,o=a.styles,s=o===void 0?{}:o,f=a.config,u=f===void 0?{}:f,m=t===ea,c=n.includes("fa-duotone")||n.includes("fad"),v=u.familyDefault==="duotone",p=e.prefix==="fad"||e.prefix==="fa-duotone";if(!m&&(c||v||p)&&(e.prefix="fad"),(n.includes("fa-brands")||n.includes("fab"))&&(e.prefix="fab"),!e.prefix&&Ci.includes(t)){var y=Object.keys(s).find(function(S){return ji.includes(S)});if(y||u.autoFetchSvg){var h=Me.get(t).defaultShortPrefixId;e.prefix=h,e.iconName=R(e.prefix,e.iconName)||e.iconName}}return(e.prefix==="fa"||i==="fa")&&(e.prefix=M()||"fas"),e}var Ti=function(){function a(){ae(this,a),this.definitions={}}return te(a,[{key:"add",value:function(){for(var t=this,e=arguments.length,r=new Array(e),i=0;i<e;i++)r[i]=arguments[i];var o=r.reduce(this._pullDefinitions,{});Object.keys(o).forEach(function(s){t.definitions[s]=l(l({},t.definitions[s]||{}),o[s]),_a(s,o[s]);var f=Ba[P][s];f&&_a(f,o[s]),Tt()})}},{key:"reset",value:function(){this.definitions={}}},{key:"_pullDefinitions",value:function(t,e){var r=e.prefix&&e.iconName&&e.icon?{0:e}:e;return Object.keys(r).map(function(i){var o=r[i],s=o.prefix,f=o.iconName,u=o.icon,m=u[2];t[s]||(t[s]={}),m.length>0&&m.forEach(function(c){typeof c=="string"&&(t[s][c]=u)}),t[s][f]=u}),t}}])}(),yn=[],H={},G={},_i=Object.keys(G);function Li(a,n){var t=n.mixoutsTo;return yn=a,H={},Object.keys(G).forEach(function(e){_i.indexOf(e)===-1&&delete G[e]}),yn.forEach(function(e){var r=e.mixout?e.mixout():{};if(Object.keys(r).forEach(function(o){typeof r[o]=="function"&&(t[o]=r[o]),da(r[o])==="object"&&Object.keys(r[o]).forEach(function(s){t[o]||(t[o]={}),t[o][s]=r[o][s]})}),e.hooks){var i=e.hooks();Object.keys(i).forEach(function(o){H[o]||(H[o]=[]),H[o].push(i[o])})}e.provides&&e.provides(G)}),t}function La(a,n){for(var t=arguments.length,e=new Array(t>2?t-2:0),r=2;r<t;r++)e[r-2]=arguments[r];var i=H[a]||[];return i.forEach(function(o){n=o.apply(null,[n].concat(e))}),n}function U(a){for(var n=arguments.length,t=new Array(n>1?n-1:0),e=1;e<n;e++)t[e-1]=arguments[e];var r=H[a]||[];r.forEach(function(i){i.apply(null,t)})}function D(){var a=arguments[0],n=Array.prototype.slice.call(arguments,1);return G[a]?G[a].apply(null,n):void 0}function $a(a){a.prefix==="fa"&&(a.prefix="fas");var n=a.iconName,t=a.prefix||M();if(n)return n=R(t,n)||n,vn($t.definitions,t,n)||vn(F.styles,t,n)}var $t=new Ti,$i=function(){d.autoReplaceSvg=!1,d.observeMutations=!1,U("noAuto")},Mi={i2svg:function(){var n=arguments.length>0&&arguments[0]!==void 0?arguments[0]:{};return _?(U("beforeI2svg",n),D("pseudoElements2svg",n),D("i2svg",n)):Promise.reject(new Error("Operation requires a DOM of some kind."))},watch:function(){var n=arguments.length>0&&arguments[0]!==void 0?arguments[0]:{},t=n.autoReplaceSvgRoot;d.autoReplaceSvg===!1&&(d.autoReplaceSvg=!0),d.observeMutations=!0,Si(function(){Ri({autoReplaceSvgRoot:t}),U("watch",n)})}},Di={icon:function(n){if(n===null)return null;if(da(n)==="object"&&n.prefix&&n.iconName)return{prefix:n.prefix,iconName:R(n.prefix,n.iconName)||n.iconName};if(Array.isArray(n)&&n.length===2){var t=n[1].indexOf("fa-")===0?n[1].slice(3):n[1],e=ha(n[0]);return{prefix:e,iconName:R(e,t)||t}}if(typeof n=="string"&&(n.indexOf("".concat(d.cssPrefix,"-"))>-1||n.match(ii))){var r=ya(n.split(" "),{skipLookups:!0});return{prefix:r.prefix||M(),iconName:R(r.prefix,r.iconName)||r.iconName}}if(typeof n=="string"){var i=M();return{prefix:i,iconName:R(i,n)||n}}}},z={noAuto:$i,config:d,dom:Mi,parse:Di,library:$t,findIconDefinition:$a,toHtml:ia},Ri=function(){var n=arguments.length>0&&arguments[0]!==void 0?arguments[0]:{},t=n.autoReplaceSvgRoot,e=t===void 0?x:t;(Object.keys(F.styles).length>0||d.autoFetchSvg)&&_&&d.autoReplaceSvg&&z.dom.i2svg({node:e})};function xa(a,n){return Object.defineProperty(a,"abstract",{get:n}),Object.defineProperty(a,"html",{get:function(){return a.abstract.map(function(e){return ia(e)})}}),Object.defineProperty(a,"node",{get:function(){if(_){var e=x.createElement("div");return e.innerHTML=a.html,e.children}}}),a}function Wi(a){var n=a.children,t=a.main,e=a.mask,r=a.attributes,i=a.styles,o=a.transform;if(Ka(o)&&t.found&&!e.found){var s=t.width,f=t.height,u={x:s/f/2,y:.5};r.style=ba(l(l({},i),{},{"transform-origin":"".concat(u.x+o.x/16,"em ").concat(u.y+o.y/16,"em")}))}return[{tag:"svg",attributes:r,children:n}]}function Ui(a){var n=a.prefix,t=a.iconName,e=a.children,r=a.attributes,i=a.symbol,o=i===!0?"".concat(n,"-").concat(d.cssPrefix,"-").concat(t):i;return[{tag:"svg",attributes:{style:"display: none;"},children:[{tag:"symbol",attributes:l(l({},r),{},{id:o}),children:e}]}]}function Yi(a){var n=["aria-label","aria-labelledby","title","role"];return n.some(function(t){return t in a})}function Za(a){var n=a.icons,t=n.main,e=n.mask,r=a.prefix,i=a.iconName,o=a.transform,s=a.symbol,f=a.maskId,u=a.extra,m=a.watchable,c=m===void 0?!1:m,v=e.found?e:t,p=v.width,y=v.height,h=[d.replacementClass,i?"".concat(d.cssPrefix,"-").concat(i):""].filter(function(E){return u.classes.indexOf(E)===-1}).filter(function(E){return E!==""||!!E}).concat(u.classes).join(" "),S={children:[],attributes:l(l({},u.attributes),{},{"data-prefix":r,"data-icon":i,class:h,role:u.attributes.role||"img",viewBox:"0 0 ".concat(p," ").concat(y)})};!Yi(u.attributes)&&!u.attributes["aria-hidden"]&&(S.attributes["aria-hidden"]="true"),c&&(S.attributes[W]="");var w=l(l({},S),{},{prefix:r,iconName:i,main:t,mask:e,maskId:f,transform:o,symbol:s,styles:l({},u.styles)}),A=e.found&&t.found?D("generateAbstractMask",w)||{children:[],attributes:{}}:D("generateAbstractIcon",w)||{children:[],attributes:{}},I=A.children,L=A.attributes;return w.children=I,w.attributes=L,s?Ui(w):Wi(w)}function xn(a){var n=a.content,t=a.width,e=a.height,r=a.transform,i=a.extra,o=a.watchable,s=o===void 0?!1:o,f=l(l({},i.attributes),{},{class:i.classes.join(" ")});s&&(f[W]="");var u=l({},i.styles);Ka(r)&&(u.transform=hi({transform:r,width:t,height:e}),u["-webkit-transform"]=u.transform);var m=ba(u);m.length>0&&(f.style=m);var c=[];return c.push({tag:"span",attributes:f,children:[n]}),c}function Xi(a){var n=a.content,t=a.extra,e=l(l({},t.attributes),{},{class:t.classes.join(" ")}),r=ba(t.styles);r.length>0&&(e.style=r);var i=[];return i.push({tag:"span",attributes:e,children:[n]}),i}var Pa=F.styles;function Ma(a){var n=a[0],t=a[1],e=a.slice(4),r=pa(e,1),i=r[0],o=null;return Array.isArray(i)?o={tag:"g",attributes:{class:"".concat(d.cssPrefix,"-").concat(Aa.GROUP)},children:[{tag:"path",attributes:{class:"".concat(d.cssPrefix,"-").concat(Aa.SECONDARY),fill:"currentColor",d:i[0]}},{tag:"path",attributes:{class:"".concat(d.cssPrefix,"-").concat(Aa.PRIMARY),fill:"currentColor",d:i[1]}}]}:o={tag:"path",attributes:{fill:"currentColor",d:i}},{found:!0,width:n,height:t,icon:o}}var Hi={found:!1,width:512,height:512};function Gi(a,n){!yt&&!d.showMissingIcons&&a&&console.error('Icon with name "'.concat(a,'" and prefix "').concat(n,'" is missing.'))}function Da(a,n){var t=n;return n==="fa"&&d.styleDefault!==null&&(n=M()),new Promise(function(e,r){if(t==="fa"){var i=_t(a)||{};a=i.iconName||a,n=i.prefix||n}if(a&&n&&Pa[n]&&Pa[n][a]){var o=Pa[n][a];return e(Ma(o))}Gi(a,n),e(l(l({},Hi),{},{icon:d.showMissingIcons&&a?D("missingIconAbstract")||{}:{}}))})}var Sn=function(){},Ra=d.measurePerformance&&oa&&oa.mark&&oa.measure?oa:{mark:Sn,measure:Sn},Q='FA "7.3.0"',Vi=function(n){return Ra.mark("".concat(Q," ").concat(n," begins")),function(){return Mt(n)}},Mt=function(n){Ra.mark("".concat(Q," ").concat(n," ends")),Ra.measure("".concat(Q," ").concat(n),"".concat(Q," ").concat(n," begins"),"".concat(Q," ").concat(n," ends"))},an={begin:Vi,end:Mt},ca=function(){};function wn(a){var n=a.getAttribute?a.getAttribute(W):null;return typeof n=="string"}function Bi(a){var n=a.getAttribute?a.getAttribute(Ga):null,t=a.getAttribute?a.getAttribute(Va):null;return n&&t}function qi(a){return a&&a.classList&&a.classList.contains&&a.classList.contains(d.replacementClass)}function Ki(){if(d.autoReplaceSvg===!0)return ma.replace;var a=ma[d.autoReplaceSvg];return a||ma.replace}function Ji(a){return x.createElementNS("http://www.w3.org/2000/svg",a)}function Qi(a){return x.createElement(a)}function Dt(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},t=n.ceFn,e=t===void 0?a.tag==="svg"?Ji:Qi:t;if(typeof a=="string")return x.createTextNode(a);var r=e(a.tag);Object.keys(a.attributes||[]).forEach(function(o){r.setAttribute(o,a.attributes[o])});var i=a.children||[];return i.forEach(function(o){r.appendChild(Dt(o,{ceFn:e}))}),r}function Zi(a){var n=" ".concat(a.outerHTML," ");return n="".concat(n,"Font Awesome fontawesome.com "),n}var ma={replace:function(n){var t=n[0];if(t.parentNode)if(n[1].forEach(function(r){t.parentNode.insertBefore(Dt(r),t)}),t.getAttribute(W)===null&&d.keepOriginalSource){var e=x.createComment(Zi(t));t.parentNode.replaceChild(e,t)}else t.remove()},nest:function(n){var t=n[0],e=n[1];if(~qa(t).indexOf(d.replacementClass))return ma.replace(n);var r=new RegExp("".concat(d.cssPrefix,"-.*"));if(delete e[0].attributes.id,e[0].attributes.class){var i=e[0].attributes.class.split(" ").reduce(function(s,f){return f===d.replacementClass||f.match(r)?s.toSvg.push(f):s.toNode.push(f),s},{toNode:[],toSvg:[]});e[0].attributes.class=i.toSvg.join(" "),i.toNode.length===0?t.removeAttribute("class"):t.setAttribute("class",i.toNode.join(" "))}var o=e.map(function(s){return ia(s)}).join(`
`);t.setAttribute(W,""),t.innerHTML=o}};function An(a){a()}function Rt(a,n){var t=typeof n=="function"?n:ca;if(a.length===0)t();else{var e=An;d.mutateApproach===ti&&(e=$.requestAnimationFrame||An),e(function(){var r=Ki(),i=an.begin("mutate");a.map(r),i(),t()})}}var nn=!1;function Wt(){nn=!0}function Wa(){nn=!1}var va=null;function kn(a){if(fn&&d.observeMutations){var n=a.treeCallback,t=n===void 0?ca:n,e=a.nodeCallback,r=e===void 0?ca:e,i=a.pseudoElementsCallback,o=i===void 0?ca:i,s=a.observeMutationsRoot,f=s===void 0?x:s;va=new fn(function(u){if(!nn){var m=M();q(u).forEach(function(c){if(c.type==="childList"&&c.addedNodes.length>0&&!wn(c.addedNodes[0])&&(d.searchPseudoElements&&o(c.target),t(c.target)),c.type==="attributes"&&c.target.parentNode&&d.searchPseudoElements&&o([c.target],!0),c.type==="attributes"&&wn(c.target)&&~fi.indexOf(c.attributeName))if(c.attributeName==="class"&&Bi(c.target)){var v=ya(qa(c.target)),p=v.prefix,y=v.iconName;c.target.setAttribute(Ga,p||m),y&&c.target.setAttribute(Va,y)}else qi(c.target)&&r(c.target)})}}),_&&va.observe(f,{childList:!0,attributes:!0,characterData:!0,subtree:!0})}}function ao(){va&&va.disconnect()}function no(a){var n=a.getAttribute("style"),t=[];return n&&(t=n.split(";").reduce(function(e,r){var i=r.split(":"),o=i[0],s=i.slice(1);return o&&s.length>0&&(e[o]=s.join(":").trim()),e},{})),t}function to(a){var n=a.getAttribute("data-prefix"),t=a.getAttribute("data-icon"),e=a.innerText!==void 0?a.innerText.trim():"",r=ya(qa(a));return r.prefix||(r.prefix=M()),n&&t&&(r.prefix=n,r.iconName=t),r.iconName&&r.prefix||(r.prefix&&e.length>0&&(r.iconName=Pi(r.prefix,a.innerText)||Qa(r.prefix,zt(a.innerText))),!r.iconName&&d.autoFetchSvg&&a.firstChild&&a.firstChild.nodeType===Node.TEXT_NODE&&(r.iconName=a.firstChild.data)),r}function eo(a){var n=q(a.attributes).reduce(function(t,e){return t.name!=="class"&&t.name!=="style"&&(t[e.name]=e.value),t},{});return n}function ro(){return{iconName:null,prefix:null,transform:C,symbol:!1,mask:{iconName:null,prefix:null,rest:[]},maskId:null,extra:{classes:[],styles:{},attributes:{}}}}function In(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{styleParser:!0},t=to(a),e=t.iconName,r=t.prefix,i=t.rest,o=eo(a),s=La("parseNodeAttributes",{},a),f=n.styleParser?no(a):[];return l({iconName:e,prefix:r,transform:C,mask:{iconName:null,prefix:null,rest:[]},maskId:null,symbol:!1,extra:{classes:i,styles:f,attributes:o}},s)}var io=F.styles;function Ut(a){var n=d.autoReplaceSvg==="nest"?In(a,{styleParser:!1}):In(a);return~n.extra.classes.indexOf(St)?D("generateLayersText",a,n):D("generateSvgReplacementMutation",a,n)}function oo(){return[].concat(O(dt),O(gt))}function Pn(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:null;if(!_)return Promise.resolve();var t=x.documentElement.classList,e=function(c){return t.add("".concat(cn,"-").concat(c))},r=function(c){return t.remove("".concat(cn,"-").concat(c))},i=d.autoFetchSvg?oo():Yn.concat(Object.keys(io));i.includes("fa")||i.push("fa");var o=[".".concat(St,":not([").concat(W,"])")].concat(i.map(function(m){return".".concat(m,":not([").concat(W,"])")})).join(", ");if(o.length===0)return Promise.resolve();var s=[];try{s=q(a.querySelectorAll(o))}catch{}if(s.length>0)e("pending"),r("complete");else return Promise.resolve();var f=an.begin("onTree"),u=s.reduce(function(m,c){try{var v=Ut(c);v&&m.push(v)}catch(p){yt||p.name==="MissingIcon"&&console.error(p)}return m},[]);return new Promise(function(m,c){Promise.all(u).then(function(v){Rt(v,function(){e("active"),e("complete"),r("pending"),typeof n=="function"&&n(),f(),m()})}).catch(function(v){f(),c(v)})})}function so(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:null;Ut(a).then(function(t){t&&Rt([t],n)})}function fo(a){return function(n){var t=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},e=(n||{}).icon?n:$a(n||{}),r=t.mask;return r&&(r=(r||{}).icon?r:$a(r||{})),a(e,l(l({},t),{},{mask:r}))}}var lo=function(n){var t=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},e=t.transform,r=e===void 0?C:e,i=t.symbol,o=i===void 0?!1:i,s=t.mask,f=s===void 0?null:s,u=t.maskId,m=u===void 0?null:u,c=t.classes,v=c===void 0?[]:c,p=t.attributes,y=p===void 0?{}:p,h=t.styles,S=h===void 0?{}:h;if(n){var w=n.prefix,A=n.iconName,I=n.icon;return xa(l({type:"icon"},n),function(){return U("beforeDOMElementCreation",{iconDefinition:n,params:t}),Za({icons:{main:Ma(I),mask:f?Ma(f.icon):{found:!1,width:null,height:null,icon:{}}},prefix:w,iconName:A,transform:l(l({},C),r),symbol:o,maskId:m,extra:{attributes:y,styles:S,classes:v}})})}},uo={mixout:function(){return{icon:fo(lo)}},hooks:function(){return{mutationObserverCallbacks:function(t){return t.treeCallback=Pn,t.nodeCallback=so,t}}},provides:function(n){n.i2svg=function(t){var e=t.node,r=e===void 0?x:e,i=t.callback,o=i===void 0?function(){}:i;return Pn(r,o)},n.generateSvgReplacementMutation=function(t,e){var r=e.iconName,i=e.prefix,o=e.transform,s=e.symbol,f=e.mask,u=e.maskId,m=e.extra;return new Promise(function(c,v){Promise.all([Da(r,i),f.iconName?Da(f.iconName,f.prefix):Promise.resolve({found:!1,width:512,height:512,icon:{}})]).then(function(p){var y=pa(p,2),h=y[0],S=y[1];c([t,Za({icons:{main:h,mask:S},prefix:i,iconName:r,transform:o,symbol:s,maskId:u,extra:m,watchable:!0})])}).catch(v)})},n.generateAbstractIcon=function(t){var e=t.children,r=t.attributes,i=t.main,o=t.transform,s=t.styles,f=ba(s);f.length>0&&(r.style=f);var u;return Ka(o)&&(u=D("generateAbstractTransformGrouping",{main:i,transform:o,containerWidth:i.width,iconWidth:i.width})),e.push(u||i.icon),{children:e,attributes:r}}}},co={mixout:function(){return{layer:function(t){var e=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},r=e.classes,i=r===void 0?[]:r;return xa({type:"layer"},function(){U("beforeDOMElementCreation",{assembler:t,params:e});var o=[];return t(function(s){Array.isArray(s)?s.map(function(f){o=o.concat(f.abstract)}):o=o.concat(s.abstract)}),[{tag:"span",attributes:{class:["".concat(d.cssPrefix,"-layers")].concat(O(i)).join(" ")},children:o}]})}}}},mo={mixout:function(){return{counter:function(t){var e=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{};e.title;var r=e.classes,i=r===void 0?[]:r,o=e.attributes,s=o===void 0?{}:o,f=e.styles,u=f===void 0?{}:f;return xa({type:"counter",content:t},function(){return U("beforeDOMElementCreation",{content:t,params:e}),Xi({content:t.toString(),extra:{attributes:s,styles:u,classes:["".concat(d.cssPrefix,"-layers-counter")].concat(O(i))}})})}}}},go={mixout:function(){return{text:function(t){var e=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{},r=e.transform,i=r===void 0?C:r,o=e.classes,s=o===void 0?[]:o,f=e.attributes,u=f===void 0?{}:f,m=e.styles,c=m===void 0?{}:m;return xa({type:"text",content:t},function(){return U("beforeDOMElementCreation",{content:t,params:e}),xn({content:t,transform:l(l({},C),i),extra:{attributes:u,styles:c,classes:["".concat(d.cssPrefix,"-layers-text")].concat(O(s))}})})}}},provides:function(n){n.generateLayersText=function(t,e){var r=e.transform,i=e.extra,o=null,s=null;if(Wn){var f=parseInt(getComputedStyle(t).fontSize,10),u=t.getBoundingClientRect();o=u.width/f,s=u.height/f}return Promise.resolve([t,xn({content:t.innerHTML,width:o,height:s,transform:r,extra:i,watchable:!0})])}}},Yt=new RegExp('"',"ug"),zn=[1105920,1112319],En=l(l(l(l({},{FontAwesome:{normal:"fas",400:"fas"}}),$e),ai),Ge),Ua=Object.keys(En).reduce(function(a,n){return a[n.toLowerCase()]=En[n],a},{}),vo=Object.keys(Ua).reduce(function(a,n){var t=Ua[n];return a[n]=t[900]||O(Object.entries(t))[0][1],a},{});function po(a){var n=a.replace(Yt,"");return zt(O(n)[0]||"")}function bo(a){var n=a.getPropertyValue("font-feature-settings").includes("ss01"),t=a.getPropertyValue("content"),e=t.replace(Yt,""),r=e.codePointAt(0),i=r>=zn[0]&&r<=zn[1],o=e.length===2?e[0]===e[1]:!1;return i||o||n}function ho(a,n){var t=a.replace(/^['"]|['"]$/g,"").toLowerCase(),e=parseInt(n),r=isNaN(e)?"normal":e;return(Ua[t]||{})[r]||vo[t]}function Fn(a,n){var t="".concat(ni).concat(n.replace(":","-"));return new Promise(function(e,r){if(a.getAttribute(t)!==null)return e();var i=q(a.children),o=i.filter(function(K){return K.getAttribute(Ca)===n})[0],s=$.getComputedStyle(a,n),f=s.getPropertyValue("font-family"),u=f.match(oi),m=s.getPropertyValue("font-weight"),c=s.getPropertyValue("content");if(o&&!u)return a.removeChild(o),e();if(u&&c!=="none"&&c!==""){var v=s.getPropertyValue("content"),p=ho(f,m),y=po(v),h=u[0].startsWith("FontAwesome"),S=bo(s),w=Qa(p,y),A=w;if(h){var I=zi(y);I.iconName&&I.prefix&&(w=I.iconName,p=I.prefix)}if(w&&!S&&(!o||o.getAttribute(Ga)!==p||o.getAttribute(Va)!==A)){a.setAttribute(t,A),o&&a.removeChild(o);var L=ro(),E=L.extra;E.attributes[Ca]=n,Da(w,p).then(function(K){var Sa=Za(l(l({},L),{},{icons:{main:K,mask:Lt()},prefix:p,iconName:A,extra:E,watchable:!0})),J=x.createElementNS("http://www.w3.org/2000/svg","svg");n==="::before"?a.insertBefore(J,a.firstChild):a.appendChild(J),J.outerHTML=Sa.map(function(wa){return ia(wa)}).join(`
`),a.removeAttribute(t),e()}).catch(r)}else e()}else e()})}function yo(a){return Promise.all([Fn(a,"::before"),Fn(a,"::after")])}function xo(a){return a.parentNode!==document.head&&!~ei.indexOf(a.tagName.toUpperCase())&&!a.getAttribute(Ca)&&(!a.parentNode||a.parentNode.tagName!=="svg")}var So=function(n){return!!n&&ht.some(function(t){return n.includes(t)})},wo=function(n){if(!n)return[];var t=new Set,e=n.split(/,(?![^()]*\))/).map(function(f){return f.trim()});e=e.flatMap(function(f){return f.includes("(")?f:f.split(",").map(function(u){return u.trim()})});var r=ua(e),i;try{for(r.s();!(i=r.n()).done;){var o=i.value;if(So(o)){var s=ht.reduce(function(f,u){return f.replace(u,"")},o);s!==""&&s!=="*"&&t.add(s)}}}catch(f){r.e(f)}finally{r.f()}return t};function On(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:!1;if(_){var t;if(n)t=a;else if(d.searchPseudoElementsFullScan)t=a.querySelectorAll("*");else{var e=new Set,r=ua(document.styleSheets),i;try{for(r.s();!(i=r.n()).done;){var o=i.value;try{var s=ua(o.cssRules),f;try{for(s.s();!(f=s.n()).done;){var u=f.value,m=wo(u.selectorText),c=ua(m),v;try{for(c.s();!(v=c.n()).done;){var p=v.value;e.add(p)}}catch(h){c.e(h)}finally{c.f()}}}catch(h){s.e(h)}finally{s.f()}}catch(h){d.searchPseudoElementsWarnings&&console.warn("Font Awesome: cannot parse stylesheet: ".concat(o.href," (").concat(h.message,`)
If it declares any Font Awesome CSS pseudo-elements, they will not be rendered as SVG icons. Add crossorigin="anonymous" to the <link>, enable searchPseudoElementsFullScan for slower but more thorough DOM parsing, or suppress this warning by setting searchPseudoElementsWarnings to false.`))}}}catch(h){r.e(h)}finally{r.f()}if(!e.size)return;var y=Array.from(e).join(", ");try{t=a.querySelectorAll(y)}catch{}}return new Promise(function(h,S){var w=q(t).filter(xo).map(yo),A=an.begin("searchPseudoElements");Wt(),Promise.all(w).then(function(){A(),Wa(),h()}).catch(function(){A(),Wa(),S()})})}}var Ao={hooks:function(){return{mutationObserverCallbacks:function(t){return t.pseudoElementsCallback=On,t}}},provides:function(n){n.pseudoElements2svg=function(t){var e=t.node,r=e===void 0?x:e;d.searchPseudoElements&&On(r)}}},Cn=!1,ko={mixout:function(){return{dom:{unwatch:function(){Wt(),Cn=!0}}}},hooks:function(){return{bootstrap:function(){kn(La("mutationObserverCallbacks",{}))},noAuto:function(){ao()},watch:function(t){var e=t.observeMutationsRoot;Cn?Wa():kn(La("mutationObserverCallbacks",{observeMutationsRoot:e}))}}}},jn=function(n){var t={size:16,x:0,y:0,flipX:!1,flipY:!1,rotate:0};return n.toLowerCase().split(" ").reduce(function(e,r){var i=r.toLowerCase().split("-"),o=i[0],s=i.slice(1).join("-");if(o&&s==="h")return e.flipX=!0,e;if(o&&s==="v")return e.flipY=!0,e;if(s=parseFloat(s),isNaN(s))return e;switch(o){case"grow":e.size=e.size+s;break;case"shrink":e.size=e.size-s;break;case"left":e.x=e.x-s;break;case"right":e.x=e.x+s;break;case"up":e.y=e.y-s;break;case"down":e.y=e.y+s;break;case"rotate":e.rotate=e.rotate+s;break}return e},t)},Io={mixout:function(){return{parse:{transform:function(t){return jn(t)}}}},hooks:function(){return{parseNodeAttributes:function(t,e){var r=e.getAttribute("data-fa-transform");return r&&(t.transform=jn(r)),t}}},provides:function(n){n.generateAbstractTransformGrouping=function(t){var e=t.main,r=t.transform,i=t.containerWidth,o=t.iconWidth,s={transform:"translate(".concat(i/2," 256)")},f="translate(".concat(r.x*32,", ").concat(r.y*32,") "),u="scale(".concat(r.size/16*(r.flipX?-1:1),", ").concat(r.size/16*(r.flipY?-1:1),") "),m="rotate(".concat(r.rotate," 0 0)"),c={transform:"".concat(f," ").concat(u," ").concat(m)},v={transform:"translate(".concat(o/2*-1," -256)")},p={outer:s,inner:c,path:v};return{tag:"g",attributes:l({},p.outer),children:[{tag:"g",attributes:l({},p.inner),children:[{tag:e.icon.tag,children:e.icon.children,attributes:l(l({},e.icon.attributes),p.path)}]}]}}}},za={x:0,y:0,width:"100%",height:"100%"};function Nn(a){var n=arguments.length>1&&arguments[1]!==void 0?arguments[1]:!0;return a.attributes&&(a.attributes.fill||n)&&(a.attributes.fill="black"),a}function Po(a){return a.tag==="g"?a.children:[a]}var zo={hooks:function(){return{parseNodeAttributes:function(t,e){var r=e.getAttribute("data-fa-mask"),i=r?ya(r.split(" ").map(function(o){return o.trim()})):Lt();return i.prefix||(i.prefix=M()),t.mask=i,t.maskId=e.getAttribute("data-fa-mask-id"),t}}},provides:function(n){n.generateAbstractMask=function(t){var e=t.children,r=t.attributes,i=t.main,o=t.mask,s=t.maskId,f=t.transform,u=i.width,m=i.icon,c=o.width,v=o.icon,p=bi({transform:f,containerWidth:c,iconWidth:u}),y={tag:"rect",attributes:l(l({},za),{},{fill:"white"})},h=m.children?{children:m.children.map(Nn)}:{},S={tag:"g",attributes:l({},p.inner),children:[Nn(l({tag:m.tag,attributes:l(l({},m.attributes),p.path)},h))]},w={tag:"g",attributes:l({},p.outer),children:[S]},A="mask-".concat(s||dn()),I="clip-".concat(s||dn()),L={tag:"mask",attributes:l(l({},za),{},{id:A,maskUnits:"userSpaceOnUse",maskContentUnits:"userSpaceOnUse"}),children:[y,w]},E={tag:"defs",children:[{tag:"clipPath",attributes:{id:I},children:Po(v)},L]};return e.push(E,{tag:"rect",attributes:l({fill:"currentColor","clip-path":"url(#".concat(I,")"),mask:"url(#".concat(A,")")},za)}),{children:e,attributes:r}}}},Eo={provides:function(n){var t=!1;$.matchMedia&&(t=$.matchMedia("(prefers-reduced-motion: reduce)").matches),n.missingIconAbstract=function(){var e=[],r={fill:"currentColor"},i={attributeType:"XML",repeatCount:"indefinite",dur:"2s"};e.push({tag:"path",attributes:l(l({},r),{},{d:"M156.5,447.7l-12.6,29.5c-18.7-9.5-35.9-21.2-51.5-34.9l22.7-22.7C127.6,430.5,141.5,440,156.5,447.7z M40.6,272H8.5 c1.4,21.2,5.4,41.7,11.7,61.1L50,321.2C45.1,305.5,41.8,289,40.6,272z M40.6,240c1.4-18.8,5.2-37,11.1-54.1l-29.5-12.6 C14.7,194.3,10,216.7,8.5,240H40.6z M64.3,156.5c7.8-14.9,17.2-28.8,28.1-41.5L69.7,92.3c-13.7,15.6-25.5,32.8-34.9,51.5 L64.3,156.5z M397,419.6c-13.9,12-29.4,22.3-46.1,30.4l11.9,29.8c20.7-9.9,39.8-22.6,56.9-37.6L397,419.6z M115,92.4 c13.9-12,29.4-22.3,46.1-30.4l-11.9-29.8c-20.7,9.9-39.8,22.6-56.8,37.6L115,92.4z M447.7,355.5c-7.8,14.9-17.2,28.8-28.1,41.5 l22.7,22.7c13.7-15.6,25.5-32.9,34.9-51.5L447.7,355.5z M471.4,272c-1.4,18.8-5.2,37-11.1,54.1l29.5,12.6 c7.5-21.1,12.2-43.5,13.6-66.8H471.4z M321.2,462c-15.7,5-32.2,8.2-49.2,9.4v32.1c21.2-1.4,41.7-5.4,61.1-11.7L321.2,462z M240,471.4c-18.8-1.4-37-5.2-54.1-11.1l-12.6,29.5c21.1,7.5,43.5,12.2,66.8,13.6V471.4z M462,190.8c5,15.7,8.2,32.2,9.4,49.2h32.1 c-1.4-21.2-5.4-41.7-11.7-61.1L462,190.8z M92.4,397c-12-13.9-22.3-29.4-30.4-46.1l-29.8,11.9c9.9,20.7,22.6,39.8,37.6,56.9 L92.4,397z M272,40.6c18.8,1.4,36.9,5.2,54.1,11.1l12.6-29.5C317.7,14.7,295.3,10,272,8.5V40.6z M190.8,50 c15.7-5,32.2-8.2,49.2-9.4V8.5c-21.2,1.4-41.7,5.4-61.1,11.7L190.8,50z M442.3,92.3L419.6,115c12,13.9,22.3,29.4,30.5,46.1 l29.8-11.9C470,128.5,457.3,109.4,442.3,92.3z M397,92.4l22.7-22.7c-15.6-13.7-32.8-25.5-51.5-34.9l-12.6,29.5 C370.4,72.1,384.4,81.5,397,92.4z"})});var o=l(l({},i),{},{attributeName:"opacity"}),s={tag:"circle",attributes:l(l({},r),{},{cx:"256",cy:"364",r:"28"}),children:[]};return t||s.children.push({tag:"animate",attributes:l(l({},i),{},{attributeName:"r",values:"28;14;28;28;14;28;"})},{tag:"animate",attributes:l(l({},o),{},{values:"1;0;1;1;0;1;"})}),e.push(s),e.push({tag:"path",attributes:l(l({},r),{},{opacity:"1",d:"M263.7,312h-16c-6.6,0-12-5.4-12-12c0-71,77.4-63.9,77.4-107.8c0-20-17.8-40.2-57.4-40.2c-29.1,0-44.3,9.6-59.2,28.7 c-3.9,5-11.1,6-16.2,2.4l-13.1-9.2c-5.6-3.9-6.9-11.8-2.6-17.2c21.2-27.2,46.4-44.7,91.2-44.7c52.3,0,97.4,29.8,97.4,80.2 c0,67.6-77.4,63.5-77.4,107.8C275.7,306.6,270.3,312,263.7,312z"}),children:t?[]:[{tag:"animate",attributes:l(l({},o),{},{values:"1;0;0;0;0;1;"})}]}),t||e.push({tag:"path",attributes:l(l({},r),{},{opacity:"0",d:"M232.5,134.5l7,168c0.3,6.4,5.6,11.5,12,11.5h9c6.4,0,11.7-5.1,12-11.5l7-168c0.3-6.8-5.2-12.5-12-12.5h-23 C237.7,122,232.2,127.7,232.5,134.5z"}),children:[{tag:"animate",attributes:l(l({},o),{},{values:"0;0;1;1;0;0;"})}]}),{tag:"g",attributes:{class:"missing"},children:e}}}},Fo={hooks:function(){return{parseNodeAttributes:function(t,e){var r=e.getAttribute("data-fa-symbol"),i=r===null?!1:r===""?!0:r;return t.symbol=i,t}}}},Oo=[xi,uo,co,mo,go,Ao,ko,Io,zo,Eo,Fo];Li(Oo,{mixoutsTo:z});z.noAuto;var B=z.config;z.library;z.dom;var Xt=z.parse;z.findIconDefinition;z.toHtml;var Co=z.icon;z.layer;z.text;z.counter;function jo(a){return a=a-0,a===a}function Ht(a){return jo(a)?a:(a=a.replace(/[_-]+(.)?/g,(n,t)=>t?t.toUpperCase():""),a.charAt(0).toLowerCase()+a.slice(1))}var No=(a,n)=>Ya.createElement("stop",{key:`${n}-${a.offset}`,offset:a.offset,stopColor:a.color,...a.opacity!==void 0&&{stopOpacity:a.opacity}});function To(a){return a.charAt(0).toUpperCase()+a.slice(1)}var X=new Map,_o=1e3;function Lo(a){if(X.has(a))return X.get(a);const n={};let t=0;const e=a.length;for(;t<e;){const r=a.indexOf(";",t),i=r===-1?e:r,o=a.slice(t,i).trim();if(o){const s=o.indexOf(":");if(s>0){const f=o.slice(0,s).trim(),u=o.slice(s+1).trim();if(f&&u){const m=Ht(f);n[m.startsWith("webkit")?To(m):m]=u}}}t=i+1}if(X.size===_o){const r=X.keys().next().value;r&&X.delete(r)}return X.set(a,n),n}function Gt(a,n,t={}){if(typeof n=="string")return n;const e=(n.children||[]).map(c=>{let v=c;return("fill"in t||t.gradientFill)&&c.tag==="path"&&"fill"in c.attributes&&(v={...c,attributes:{...c.attributes,fill:void 0}}),Gt(a,v)}),r=n.attributes||{},i={};for(const[c,v]of Object.entries(r))switch(!0){case c==="class":{i.className=v;break}case c==="style":{i.style=Lo(String(v));break}case c.startsWith("aria-"):case c.startsWith("data-"):{i[c.toLowerCase()]=v;break}default:i[Ht(c)]=v}const{style:o,role:s,"aria-label":f,gradientFill:u,...m}=t;if(o&&(i.style=i.style?{...i.style,...o}:o),s&&(i.role=s),f&&(i["aria-label"]=f,i["aria-hidden"]="false"),u){i.fill=`url(#${u.id})`;const{type:c,stops:v=[],...p}=u;e.unshift(a(c==="linear"?"linearGradient":"radialGradient",{...p,id:u.id},v.map(No)))}return a(n.tag,{...i,...m},...e)}var $o=Gt.bind(null,Ya.createElement),Tn=(a,n)=>{const t=Jt.useId();return a||(n?t:void 0)},Mo=class{constructor(a="react-fontawesome"){this.enabled=!1;let n=!1;try{n=typeof process<"u"&&!1}catch{}this.scope=a,this.enabled=n}log(...a){this.enabled&&console.log(`[${this.scope}]`,...a)}warn(...a){this.enabled&&console.warn(`[${this.scope}]`,...a)}error(...a){this.enabled&&console.error(`[${this.scope}]`,...a)}},Do="searchPseudoElementsFullScan"in B&&typeof B.searchPseudoElementsFullScan=="boolean"?"7.0.0":"6.0.0",Ro=Number.parseInt(Do)>=7,Wo=()=>Ro,na="fa",k={beat:"fa-beat",fade:"fa-fade",beatFade:"fa-beat-fade",bounce:"fa-bounce",shake:"fa-shake",spin:"fa-spin",spinPulse:"fa-spin-pulse",spinReverse:"fa-spin-reverse",pulse:"fa-pulse",flip360:"fa-flip-360",buzz:"fa-buzz",float:"fa-float",jello:"fa-jello",spinSnap:"fa-spin-snap",spinSnap4:"fa-spin-snap-4",spinSnap8:"fa-spin-snap-8",swing:"fa-swing",wag:"fa-wag"},Uo={left:"fa-pull-left",right:"fa-pull-right"},Yo={90:"fa-rotate-90",180:"fa-rotate-180",270:"fa-rotate-270"},Xo={"2xs":"fa-2xs",xs:"fa-xs",sm:"fa-sm",lg:"fa-lg",xl:"fa-xl","2xl":"fa-2xl","1x":"fa-1x","2x":"fa-2x","3x":"fa-3x","4x":"fa-4x","5x":"fa-5x","6x":"fa-6x","7x":"fa-7x","8x":"fa-8x","9x":"fa-9x","10x":"fa-10x"},j={border:"fa-border",fixedWidth:"fa-fw",flip:"fa-flip",flipHorizontal:"fa-flip-horizontal",flipVertical:"fa-flip-vertical",inverse:"fa-inverse",rotateBy:"fa-rotate-by",swapOpacity:"fa-swap-opacity",widthAuto:"fa-width-auto"};function Ho(a){const n=B.cssPrefix||B.familyPrefix||na;return n===na?a:a.replace(new RegExp(String.raw`(?<=^|\s)${na}-`,"g"),`${n}-`)}function Go(a){const{beat:n,fade:t,beatFade:e,bounce:r,shake:i,spin:o,spinPulse:s,spinReverse:f,pulse:u,fixedWidth:m,inverse:c,border:v,flip:p,size:y,rotation:h,pull:S,swapOpacity:w,rotateBy:A,widthAuto:I,flip360:L,buzz:E,float:K,jello:Sa,spinSnap:J,spinSnap4:wa,spinSnap8:Bt,swing:qt,wag:Kt,className:tn}=a,b=[];return tn&&b.push(...tn.split(" ")),n&&b.push(k.beat),t&&b.push(k.fade),e&&b.push(k.beatFade),r&&b.push(k.bounce),i&&b.push(k.shake),o&&b.push(k.spin),f&&b.push(k.spinReverse),s&&b.push(k.spinPulse),u&&b.push(k.pulse),m&&b.push(j.fixedWidth),c&&b.push(j.inverse),v&&b.push(j.border),p===!0&&b.push(j.flip),(p==="horizontal"||p==="both")&&b.push(j.flipHorizontal),(p==="vertical"||p==="both")&&b.push(j.flipVertical),y!=null&&b.push(Xo[y]),h!=null&&h!==0&&b.push(Yo[h]),S!=null&&b.push(Uo[S]),w&&b.push(j.swapOpacity),Wo()?(A&&b.push(j.rotateBy),I&&b.push(j.widthAuto),L&&b.push(k.flip360),E&&b.push(k.buzz),K&&b.push(k.float),Sa&&b.push(k.jello),J&&b.push(k.spinSnap),wa&&b.push(k.spinSnap4),Bt&&b.push(k.spinSnap8),qt&&b.push(k.swing),Kt&&b.push(k.wag),(B.cssPrefix||B.familyPrefix||na)===na?b:b.map(Ho)):b}var Vo=a=>typeof a=="object"&&"icon"in a&&!!a.icon;function _n(a){if(a)return Vo(a)?a:Xt.icon(a)}function Bo(a){return Object.keys(a)}var Ln=new Mo("FontAwesomeIcon"),Vt={border:!1,className:"",mask:void 0,maskId:void 0,fixedWidth:!1,inverse:!1,flip:!1,icon:void 0,listItem:!1,pull:void 0,pulse:!1,rotation:void 0,rotateBy:!1,size:void 0,spin:!1,spinPulse:!1,spinReverse:!1,beat:!1,fade:!1,beatFade:!1,bounce:!1,shake:!1,symbol:!1,title:"",titleId:void 0,transform:void 0,swapOpacity:!1,widthAuto:!1,flip360:!1,buzz:!1,float:!1,jello:!1,spinSnap:!1,spinSnap4:!1,spinSnap8:!1,swing:!1,wag:!1},qo=new Set(Object.keys(Vt)),Ko=Ya.forwardRef((a,n)=>{const t={...Vt,...a},{icon:e,mask:r,symbol:i,title:o,titleId:s,maskId:f,transform:u}=t,m=Tn(f,!!r),c=Tn(s,!!o),v=_n(e);if(!v)return Ln.error("Icon lookup is undefined",e),null;const p=Go(t),y=typeof u=="string"?Xt.transform(u):u,h=_n(r),S=Co(v,{...p.length>0&&{classes:p},...y&&{transform:y},...h&&{mask:h},symbol:i,title:o,titleId:c,maskId:m});if(!S)return Ln.error("Could not find icon",v),null;const{abstract:w}=S,A={ref:n};for(const I of Bo(t))qo.has(I)||(A[I]=t[I]);return $o(w[0],A)});Ko.displayName="FontAwesomeIcon";/*!
 * Font Awesome Free 7.3.0 by @fontawesome - https://fontawesome.com
 * License - https://fontawesome.com/license/free (Icons: CC BY 4.0, Fonts: SIL OFL 1.1, Code: MIT License)
 * Copyright 2026 Fonticons, Inc.
 */var Zo={prefix:"fas",iconName:"play",icon:[448,512,[9654],"f04b","M91.2 36.9c-12.4-6.8-27.4-6.5-39.6 .7S32 57.9 32 72l0 368c0 14.1 7.5 27.2 19.6 34.4s27.2 7.5 39.6 .7l336-184c12.8-7 20.8-20.5 20.8-35.1s-8-28.1-20.8-35.1l-336-184z"]},as={prefix:"fas",iconName:"pause",icon:[384,512,[9208],"f04c","M48 32C21.5 32 0 53.5 0 80L0 432c0 26.5 21.5 48 48 48l64 0c26.5 0 48-21.5 48-48l0-352c0-26.5-21.5-48-48-48L48 32zm224 0c-26.5 0-48 21.5-48 48l0 352c0 26.5 21.5 48 48 48l64 0c26.5 0 48-21.5 48-48l0-352c0-26.5-21.5-48-48-48l-64 0z"]},ns={prefix:"fas",iconName:"angles-right",icon:[448,512,[187,"angle-double-right"],"f101","M439.1 278.6c12.5-12.5 12.5-32.8 0-45.3l-160-160c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L371.2 256 233.9 393.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0l160-160zm-352 160l160-160c12.5-12.5 12.5-32.8 0-45.3l-160-160c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L179.2 256 41.9 393.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0z"]},ts={prefix:"fas",iconName:"angles-left",icon:[448,512,[171,"angle-double-left"],"f100","M9.4 233.4c-12.5 12.5-12.5 32.8 0 45.3l160 160c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L77.3 256 214.6 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0l-160 160zm352-160l-160 160c-12.5 12.5-12.5 32.8 0 45.3l160 160c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L269.3 256 406.6 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0z"]};export{Ko as F,Zo as a,ns as b,ts as c,as as f};
