// ITLDesk: troca de chaves na conexão TCP do hbbs (porta 21116).
//
// O cliente ITLDesk logado (com token) espera o hbbs abrir a conversa com um
// KeyExchange assinado pela chave do servidor e só depois manda o pedido de
// conexão, já criptografado. O hbbs original não faz isso, e o cliente desiste
// com "Failed to secure tcp: deadline has elapsed".
//
// Clientes que não pedem a troca mandam a primeira mensagem logo após conectar;
// por isso o hbbs só oferece a troca quando o cliente fica calado por
// OFFER_AFTER_MS, e segue sem criptografia se a resposta não for um KeyExchange.
// Usa a versão 0 do protocolo: uma única chave simétrica para os dois sentidos.

use hbb_common::{
    bytes::Bytes,
    protobuf::Message as _,
    rendezvous_proto::{rendezvous_message, KeyExchange, RendezvousMessage},
    sodiumoxide::crypto::{box_, secretbox::Key, sign},
    tcp::Encrypt,
};

pub const OFFER_AFTER_MS: u64 = 700;

/// Mensagem de abertura (chave pública efêmera assinada) e a chave secreta
/// efêmera para abrir a resposta.
pub fn offer(sk: &sign::SecretKey) -> Option<(Bytes, box_::SecretKey)> {
    let (our_pk, our_sk) = box_::gen_keypair();
    let mut msg = RendezvousMessage::new();
    msg.set_key_exchange(KeyExchange {
        keys: vec![sign::sign(&our_pk.0, sk).into()],
        ..Default::default()
    });
    msg.write_to_bytes().ok().map(|b| (Bytes::from(b), our_sk))
}

/// Chave simétrica enviada pelo cliente, se `bytes` for a resposta à troca.
pub fn accept(bytes: &[u8], our_sk: &box_::SecretKey) -> Option<Key> {
    let msg = RendezvousMessage::parse_from_bytes(bytes).ok()?;
    match msg.union {
        Some(rendezvous_message::Union::KeyExchange(ex)) if ex.keys.len() == 2 => {
            Encrypt::decode(&ex.keys[1], &ex.keys[0], our_sk).ok()
        }
        _ => None,
    }
}
