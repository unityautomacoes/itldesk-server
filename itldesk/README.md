# ITLDesk Server

Servidor próprio de acesso remoto ITLDesk, baseado no
[RustDesk Server](https://github.com/rustdesk/rustdesk-server) (licença AGPL-3.0).

São dois serviços:

| Serviço | Função | Portas |
|---|---|---|
| `hbbs` | Servidor de ID / Rendezvous (registra os PCs, faz o "aperto de mão") | 21115/tcp, 21116/tcp+udp, 21118/tcp |
| `hbbr` | Servidor Relay (repassa o tráfego quando não há conexão direta) | 21117/tcp, 21119/tcp |

> 21118 e 21119 só são necessárias para o cliente web. O mínimo é **21115-21117 TCP + 21116 UDP**.

---

## Opção 1 — Linux VPS com Docker (recomendado)

```bash
git clone <seu-repositorio-itldesk> itldesk-server
cd itldesk-server/itldesk
cp .env.example .env          # edite ITLDESK_HOST com seu IP público ou domínio
docker compose up -d --build
```

Libere no firewall do servidor (e do provedor de nuvem):

```bash
sudo ufw allow 21115:21117/tcp
sudo ufw allow 21116/udp
sudo ufw allow 21118:21119/tcp   # opcional (web client)
```

## Opção 2 — Servidor Windows

1. Instale o Rust (https://rustup.rs) e o Visual Studio Build Tools (C++).
2. Na raiz do repositório:
   ```powershell
   powershell -ExecutionPolicy Bypass -File itldesk\build-windows.ps1
   ```
3. Em `itldesk\dist`, rode (cada um em um terminal ou como serviço com NSSM):
   ```powershell
   .\hbbr.exe -k _
   .\hbbs.exe -r SEU_IP_OU_DOMINIO:21117 -k _
   ```
4. Libere as portas no Firewall do Windows e redirecione-as no roteador.

## Opção 3 — Linux sem Docker (systemd)

Compile com `cargo build --release`, copie `hbbs`/`hbbr` para `/usr/bin/` e use
`systemd/itldesk-hbbs.service` e `systemd/itldesk-hbbr.service`
(diretório de trabalho `/var/lib/itldesk-server/`, logs em `/var/log/itldesk-server/`).

---

## Chave pública (obrigatório para os clientes)

No primeiro start o `hbbs` gera o par de chaves em `data/`:

- `id_ed25519` — chave **privada** (faça backup, nunca compartilhe)
- `id_ed25519.pub` — chave **pública** (vai nos clientes)

```bash
cat itldesk/data/id_ed25519.pub
```

Como os serviços rodam com `-k _`, **só clientes configurados com essa chave conseguem usar o servidor**.

## Configurar os clientes

No cliente (RustDesk ou o seu cliente ITLDesk personalizado):
**Configurações → Rede → Servidor de ID/Relay**

| Campo | Valor |
|---|---|
| Servidor de ID | `SEU_IP_OU_DOMINIO` |
| Servidor Relay | `SEU_IP_OU_DOMINIO` |
| Chave | conteúdo de `id_ed25519.pub` |

## Cobrança (planos, técnicos e Mercado Pago)

Painel em `https://SEU_DOMINIO/cobranca/` (mesmo usuário administrador do painel `/_admin/`).

- **Venda por técnico**: cada cliente tem uma assinatura (plano + ciclo mensal/anual) e os
  técnicos criados na tela do cliente. O plano limita técnicos e informa o limite de dispositivos.
- **Bloqueio**: o `hbbs` exige login no app (`ITLDESK_MUST_LOGIN=Y`) e só aceita técnicos de
  assinaturas em teste, ativas ou em carência. A lista é gravada pelo serviço de cobrança em
  `data/itldesk_allowed_users.json` e relida a cada 15 s. Administradores do painel sempre passam.
- **Vencimento**: a fatura do próximo período é gerada 7 dias antes; depois do vencimento há
  `BILLING_GRACE_DAYS` (padrão 5) dias de carência e então o acesso é bloqueado até o pagamento.
- **Mercado Pago**: defina `MP_ACCESS_TOKEN` no `.env` (credencial de produção ou `TEST-...`).
  Cada fatura vira um link do Checkout Pro (Pix, boleto e cartão). O pagamento é confirmado pelo
  webhook `https://SEU_DOMINIO/cobranca/webhooks/mercadopago` (configure-o no painel do Mercado Pago,
  evento "Pagamentos", e copie a assinatura secreta para `MP_WEBHOOK_SECRET`) e, como reserva, por
  consulta automática a cada 5 minutos. Sem token, as faturas são baixadas manualmente.

Testes:

```bash
python3 tests/hbbs_login_check.py /opt/itldesk/itldesk          # bloqueio no hbbs
docker exec -i itldesk-billing python3 - < tests/billing_flow_check.py   # fluxo de cobrança (cópia dos bancos)
```

## Diagnóstico

```bash
docker compose logs -f
docker exec itldesk-hbbs itldesk-utils doctor SEU_IP_OU_DOMINIO
```

---

## Licença

Este projeto deriva do RustDesk Server e continua sob **AGPL-3.0**: se você
distribuir ou disponibilizar uma versão modificada, deve publicar o código-fonte
dessas modificações. "RustDesk" é marca de seus titulares — por isso o produto
usa o nome próprio **ITLDesk**.
