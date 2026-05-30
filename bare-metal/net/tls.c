#include "tls.h"
#include "tcp.h"
#include "net.h"
#include "../crypto/sha256.h"
#include "../crypto/chacha20poly1305.h"
#include "../crypto/x25519.h"
#include "../crypto/ecdsa.h"
#include "../x509/x509.h"
#include "../drivers/rtc.h"
#include "../lib/printf.h"
#include <string.h>
#include <stddef.h>

/* TLS 1.3 client: TLS_CHACHA20_POLY1305_SHA256 + x25519, full certificate
 * chain validation (see x509/). Single connection. */

struct tls_conn {
    tcp_conn_t *tcp;
    uint8_t c_key[32], c_iv[12]; uint64_t c_seq;   /* client write */
    uint8_t s_key[32], s_iv[12]; uint64_t s_seq;   /* server read */
    int have_skeys;
    uint8_t app[16400]; int applen, apppos;        /* decrypted-app leftover */
};
static struct tls_conn g;

/* ── RNG (rdtsc-mixed xorshift; ephemeral keys only) ──────────────────────── */
static uint64_t rng;
static uint64_t rdtsc(void){ uint32_t a,d; __asm__ volatile("rdtsc":"=a"(a),"=d"(d)); return ((uint64_t)d<<32)|a; }
static void rng_bytes(uint8_t *b, int n){
    if(!rng) rng = rdtsc() | 1;
    for(int i=0;i<n;i++){ rng^=rng<<13; rng^=rng>>7; rng^=rng<<17; rng+=rdtsc(); b[i]=rng>>56; }
}

/* ── TCP byte stream ──────────────────────────────────────────────────────── */
static uint8_t tin[16600]; static int tin_pos, tin_len;
static int rd_bytes(uint8_t *dst, int n){
    int got=0;
    while(got<n){
        if(tin_pos>=tin_len){ int r=tcp_recv(g.tcp, tin, sizeof(tin)); if(r<=0) return got; tin_len=r; tin_pos=0; }
        int take=tin_len-tin_pos; if(take>n-got) take=n-got;
        memcpy(dst+got, tin+tin_pos, take); tin_pos+=take; got+=take;
    }
    return got;
}

static void make_nonce(const uint8_t iv[12], uint64_t seq, uint8_t nonce[12]){
    memcpy(nonce, iv, 12);
    for(int i=0;i<8;i++) nonce[11-i] ^= (seq>>(8*i))&0xFF;
}

/* Read one TLS record; decrypt if encrypted. CCS records are skipped.
 * Returns inner content type in *ctype, plaintext in out. */
static int recv_record(uint8_t *out, int *outlen, int *ctype){
    uint8_t hdr[5];
    if(rd_bytes(hdr,5)!=5) return -1;
    int len=(hdr[3]<<8)|hdr[4];
    static uint8_t pl[16600];
    if(len<0 || len>(int)sizeof(pl)) return -1;
    if(rd_bytes(pl,len)!=len) return -1;
    if(hdr[0]==20) return recv_record(out,outlen,ctype);          /* ChangeCipherSpec: ignore */
    if(hdr[0]==23 && g.have_skeys){
        int ctlen=len-16; if(ctlen<0) return -1;
        uint8_t nonce[12]; make_nonce(g.s_iv, g.s_seq, nonce); g.s_seq++;
        static uint8_t plain[16600];
        if(aead_open(g.s_key, nonce, hdr, 5, pl, ctlen, pl+ctlen, plain)!=0){ printf("[tls] AEAD auth fail\n"); return -1; }
        int n=ctlen; while(n>0 && plain[n-1]==0) n--; if(n==0) return -1;
        *ctype=plain[n-1]; *outlen=n-1; memcpy(out, plain, n-1);
        return 0;
    }
    *ctype=hdr[0]; *outlen=len; memcpy(out, pl, len);             /* plaintext (ServerHello/alert) */
    return 0;
}

/* ── Handshake message reassembly ─────────────────────────────────────────── */
static uint8_t hsb[70000]; static int hsb_len, hsb_pos;
static int hs_refill(void){
    static uint8_t tmp[16600]; int tl, ct;
    for(;;){
        if(recv_record(tmp,&tl,&ct)!=0) return -1;
        if(ct==21){ printf("[tls] alert during handshake\n"); return -1; }
        if(ct==22){ if(hsb_len+tl>(int)sizeof(hsb)) return -1; memcpy(hsb+hsb_len,tmp,tl); hsb_len+=tl; return 0; }
        /* ignore other content types mid-handshake */
    }
}
static int next_hs(uint8_t **msg, int *mlen){
    while(hsb_len-hsb_pos<4){ if(hs_refill()) return -1; }
    int len=(hsb[hsb_pos+1]<<16)|(hsb[hsb_pos+2]<<8)|hsb[hsb_pos+3];
    while(hsb_len-hsb_pos<4+len){ if(hs_refill()) return -1; }
    *msg=hsb+hsb_pos; *mlen=4+len; hsb_pos+=4+len;
    return 0;
}

/* ── record send ──────────────────────────────────────────────────────────── */
static int send_plain(uint8_t type, const uint8_t *data, int len){
    static uint8_t rec[16700];
    rec[0]=type; rec[1]=3; rec[2]=3; rec[3]=len>>8; rec[4]=len&0xFF;
    memcpy(rec+5, data, len);
    return tcp_send(g.tcp, rec, 5+len);
}
static int send_record(uint8_t inner_type, const uint8_t *data, int len){
    static uint8_t inner[16500], rec[16700]; uint8_t tag[16];
    memcpy(inner, data, len); inner[len]=inner_type; int ilen=len+1;
    uint8_t hdr[5]={23,3,3,(ilen+16)>>8,(ilen+16)&0xFF};
    uint8_t nonce[12]; make_nonce(g.c_iv, g.c_seq, nonce); g.c_seq++;
    aead_seal(g.c_key, nonce, hdr, 5, inner, ilen, rec+5, tag);
    memcpy(rec, hdr, 5); memcpy(rec+5+ilen, tag, 16);
    return tcp_send(g.tcp, rec, 5+ilen+16);
}

/* ── key schedule ─────────────────────────────────────────────────────────── */
static void expand_label(const uint8_t secret[32], const char *label,
                         const uint8_t *ctx, int ctxlen, uint8_t *out, int outlen){
    uint8_t info[2+1+64+1+64]; int o=0; int ll=(int)strlen(label);
    info[o++]=outlen>>8; info[o++]=outlen&0xFF;
    info[o++]=6+ll;
    memcpy(info+o,"tls13 ",6); o+=6; memcpy(info+o,label,ll); o+=ll;
    info[o++]=ctxlen; if(ctxlen){ memcpy(info+o,ctx,ctxlen); o+=ctxlen; }
    hkdf_expand(secret, info, o, out, outlen);
}
static void derive_secret(const uint8_t secret[32], const char *label,
                          const uint8_t thash[32], uint8_t out[32]){
    expand_label(secret, label, thash, 32, out, 32);
}
static void traffic_keys(const uint8_t secret[32], uint8_t key[32], uint8_t iv[12]){
    expand_label(secret, "key", NULL, 0, key, 32);
    expand_label(secret, "iv",  NULL, 0, iv, 12);
}
static void th_hash(const sha256_ctx *live, uint8_t out[32]){ sha256_ctx c=*live; sha256_final(&c,out); }

/* ── handshake ────────────────────────────────────────────────────────────── */
tls_conn_t *tls_connect(uint32_t ip, uint16_t port, const char *sni){
    memset(&g, 0, sizeof(g));
    tin_pos=tin_len=hsb_len=hsb_pos=0;

    g.tcp = tcp_connect(ip, port);
    if(!g.tcp){ printf("[tls] TCP connect failed\n"); return NULL; }

    /* our ephemeral x25519 keypair */
    uint8_t priv[32], pub[32];
    rng_bytes(priv, 32);
    priv[0]&=248; priv[31]=(priv[31]&127)|64;
    x25519_base(pub, priv);

    sha256_ctx th; sha256_init(&th);

    /* ── ClientHello ──────────────────────────────────────────────────────── */
    static uint8_t ext[1024]; int e=0;
    int hostlen=(int)strlen(sni);
    /* SNI */
    ext[e++]=0x00; ext[e++]=0x00; int l=5+hostlen; ext[e++]=l>>8; ext[e++]=l&0xFF;
    l=3+hostlen; ext[e++]=l>>8; ext[e++]=l&0xFF; ext[e++]=0; ext[e++]=hostlen>>8; ext[e++]=hostlen&0xFF;
    memcpy(ext+e, sni, hostlen); e+=hostlen;
    /* supported_groups: x25519 */
    ext[e++]=0x00; ext[e++]=0x0a; ext[e++]=0; ext[e++]=4; ext[e++]=0; ext[e++]=2; ext[e++]=0x00; ext[e++]=0x1d;
    /* signature_algorithms */
    { static const uint8_t sa[]={0x04,0x03,0x05,0x03,0x08,0x04,0x04,0x01,0x08,0x05,0x05,0x01};
      ext[e++]=0x00; ext[e++]=0x0d; int sl=sizeof(sa); ext[e++]=(sl+2)>>8; ext[e++]=(sl+2)&0xFF;
      ext[e++]=sl>>8; ext[e++]=sl&0xFF; memcpy(ext+e,sa,sl); e+=sl; }
    /* supported_versions: TLS 1.3 */
    ext[e++]=0x00; ext[e++]=0x2b; ext[e++]=0; ext[e++]=3; ext[e++]=2; ext[e++]=0x03; ext[e++]=0x04;
    /* key_share: x25519 */
    ext[e++]=0x00; ext[e++]=0x33; ext[e++]=0; ext[e++]=38; ext[e++]=0; ext[e++]=36;
    ext[e++]=0x00; ext[e++]=0x1d; ext[e++]=0; ext[e++]=32; memcpy(ext+e,pub,32); e+=32;
    /* ALPN: http/1.1 */
    ext[e++]=0x00; ext[e++]=0x10; ext[e++]=0; ext[e++]=11; ext[e++]=0; ext[e++]=9; ext[e++]=8;
    memcpy(ext+e,"http/1.1",8); e+=8;

    static uint8_t ch[2048]; int b=0;
    ch[b++]=0x03; ch[b++]=0x03;                  /* legacy_version */
    rng_bytes(ch+b, 32); b+=32;                  /* random */
    ch[b++]=32; rng_bytes(ch+b,32); b+=32;       /* legacy_session_id */
    ch[b++]=0; ch[b++]=2; ch[b++]=0x13; ch[b++]=0x03;  /* cipher_suites: CHACHA20_POLY1305 */
    ch[b++]=1; ch[b++]=0;                         /* compression: null */
    ch[b++]=e>>8; ch[b++]=e&0xFF; memcpy(ch+b,ext,e); b+=e;

    static uint8_t hs[2200]; int h=0;
    hs[h++]=0x01; hs[h++]=(b>>16)&0xFF; hs[h++]=(b>>8)&0xFF; hs[h++]=b&0xFF;
    memcpy(hs+h, ch, b); h+=b;
    sha256_update(&th, hs, h);
    send_plain(22, hs, h);

    /* ── ServerHello ──────────────────────────────────────────────────────── */
    uint8_t *msg; int mlen;
    if(next_hs(&msg,&mlen) || msg[0]!=0x02){ printf("[tls] no ServerHello\n"); return NULL; }
    {
        const uint8_t *p=msg+4;                  /* version(2) random(32) */
        int sidlen=p[34]; const uint8_t *q=p+35+sidlen;
        if(q[0]!=0x13 || q[1]!=0x03){ printf("[tls] server did not pick CHACHA20\n"); return NULL; }
        int extlen=(q[3]<<8)|q[4]; const uint8_t *ep=q+5, *eend=ep+extlen;
        const uint8_t *spub=NULL;
        while(ep+4<=eend){
            int et=(ep[0]<<8)|ep[1], el=(ep[2]<<8)|ep[3]; const uint8_t *ed=ep+4;
            if(et==0x0033){ /* key_share: group(2) klen(2) key */
                if(((ed[0]<<8)|ed[1])==0x001d) spub=ed+4;
            }
            ep=ed+el;
        }
        if(!spub){ printf("[tls] no server key_share\n"); return NULL; }
        sha256_update(&th, msg, mlen);
        uint8_t th_sh[32]; th_hash(&th, th_sh);

        /* shared secret + handshake key schedule */
        uint8_t shared[32]; x25519(shared, priv, spub);
        uint8_t psk[32]={0}, early[32], d1[32], hs_secret[32], c_hs[32], s_hs[32], empty[32];
        sha256("",0,empty);
        hkdf_extract(NULL,0, psk,32, early);
        derive_secret(early,"derived",empty, d1);
        hkdf_extract(d1,32, shared,32, hs_secret);
        derive_secret(hs_secret,"c hs traffic",th_sh, c_hs);
        derive_secret(hs_secret,"s hs traffic",th_sh, s_hs);
        traffic_keys(c_hs, g.c_key, g.c_iv);
        traffic_keys(s_hs, g.s_key, g.s_iv);
        g.c_seq=g.s_seq=0; g.have_skeys=1;

        /* ── server flight (encrypted) ─────────────────────────────────────── */
        /* EncryptedExtensions */
        if(next_hs(&msg,&mlen) || msg[0]!=0x08){ printf("[tls] expected EncryptedExtensions\n"); return NULL; }
        sha256_update(&th, msg, mlen);

        /* Certificate */
        if(next_hs(&msg,&mlen) || msg[0]!=0x0b){ printf("[tls] expected Certificate\n"); return NULL; }
        {
            const uint8_t *p2=msg+4;
            int ctxlen=p2[0]; p2+=1+ctxlen; p2+=3;          /* skip context + list length */
            int c1=(p2[0]<<16)|(p2[1]<<8)|p2[2]; p2+=3;
            const uint8_t *leaf=p2; int leaf_len=c1; p2+=c1;
            int ex1=(p2[0]<<8)|p2[1]; p2+=2+ex1;
            int c2=(p2[0]<<16)|(p2[1]<<8)|p2[2]; p2+=3;
            const uint8_t *inter=p2; int inter_len=c2;
            uint64_t now=rtc_now();
            if(x509_validate_chain(leaf, leaf_len, inter, inter_len, sni, now)!=0){
                printf("[tls] CERTIFICATE VALIDATION FAILED — aborting\n"); return NULL;
            }
            printf("[tls] certificate chain validated (host %s)\n", sni);
            /* stash leaf pubkey for CertificateVerify */
            static x509_cert leafc; x509_parse(leaf, leaf_len, &leafc);
            uint8_t th_cert[32]; sha256_update(&th, msg, mlen); th_hash(&th, th_cert);

            /* CertificateVerify */
            if(next_hs(&msg,&mlen) || msg[0]!=0x0f){ printf("[tls] expected CertificateVerify\n"); return NULL; }
            {
                int scheme=(msg[4]<<8)|msg[5];
                int siglen=(msg[6]<<8)|msg[7];
                const uint8_t *sig=msg+8;
                static uint8_t cvc[64+34+32]; int o=0;
                for(int i=0;i<64;i++) cvc[o++]=0x20;
                const char *ctxstr="TLS 1.3, server CertificateVerify";
                memcpy(cvc+o, ctxstr, 33); o+=33; cvc[o++]=0;
                memcpy(cvc+o, th_cert, 32); o+=32;
                uint8_t digest[32]; sha256(cvc, o, digest);
                int ok = (scheme==0x0403 && leafc.pub_curve==CURVE_P256)
                       ? (ecdsa_verify_p256(leafc.pub+1, digest, 32, sig, siglen)==0) : 0;
                if(!ok){ printf("[tls] CertificateVerify failed (scheme 0x%x)\n", scheme); return NULL; }
                printf("[tls] CertificateVerify OK (server proved key ownership)\n");
                sha256_update(&th, msg, mlen);
            }
        }
        uint8_t th_cv[32]; th_hash(&th, th_cv);

        /* server Finished */
        if(next_hs(&msg,&mlen) || msg[0]!=0x14){ printf("[tls] expected Finished\n"); return NULL; }
        {
            uint8_t fk[32], want[32];
            expand_label(s_hs,"finished",NULL,0, fk,32);
            hmac_sha256(fk,32, th_cv,32, want);
            if(memcmp(want, msg+4, 32)!=0){ printf("[tls] server Finished MAC mismatch\n"); return NULL; }
            printf("[tls] server Finished verified — handshake authentic\n");
            sha256_update(&th, msg, mlen);
        }
        uint8_t th_sf[32]; th_hash(&th, th_sf);

        /* application key schedule */
        uint8_t d2[32], master[32], c_ap[32], s_ap[32];
        derive_secret(hs_secret,"derived",empty, d2);
        hkdf_extract(d2,32, psk,32, master);
        derive_secret(master,"c ap traffic",th_sf, c_ap);
        derive_secret(master,"s ap traffic",th_sf, s_ap);

        /* client Finished (with handshake keys), then CCS for compat */
        uint8_t one=1; send_plain(20, &one, 1);
        {
            uint8_t cfk[32], vd[32];
            expand_label(c_hs,"finished",NULL,0, cfk,32);
            hmac_sha256(cfk,32, th_sf,32, vd);
            uint8_t fin[36]; fin[0]=0x14; fin[1]=0; fin[2]=0; fin[3]=32; memcpy(fin+4, vd, 32);
            send_record(22, fin, 36);
        }

        /* switch to application traffic keys */
        traffic_keys(c_ap, g.c_key, g.c_iv);
        traffic_keys(s_ap, g.s_key, g.s_iv);
        g.c_seq=g.s_seq=0;
    }

    printf("[tls] handshake complete — encrypted channel up\n");
    return &g;
}

int tls_send(tls_conn_t *c, const void *buf, int len){
    (void)c;
    const uint8_t *p=buf; int sent=0;
    while(sent<len){ int chunk=len-sent>16000?16000:len-sent; if(send_record(23,p+sent,chunk)<0) return -1; sent+=chunk; }
    return sent;
}

int tls_recv(tls_conn_t *c, void *buf, int maxlen){
    (void)c;
    if(g.apppos<g.applen){
        int n=g.applen-g.apppos; if(n>maxlen) n=maxlen;
        memcpy(buf, g.app+g.apppos, n); g.apppos+=n; return n;
    }
    for(;;){
        int ctype; int len;
        if(recv_record(g.app,&len,&ctype)!=0) return 0;
        if(ctype==21) return 0;             /* close_notify */
        if(ctype==22) continue;             /* post-handshake (NewSessionTicket) — ignore */
        if(ctype==23){
            g.applen=len; g.apppos=0;
            int n=len; if(n>maxlen) n=maxlen;
            memcpy(buf, g.app, n); g.apppos=n; return n;
        }
    }
}

void tls_close(tls_conn_t *c){
    uint8_t alert[2]={1,0};                  /* warning, close_notify */
    send_record(21, alert, 2);
    tcp_close(g.tcp);
    (void)c;
}
