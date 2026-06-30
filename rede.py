import socket
import json


# manda uma msg (dict) pro ip:porta e devolve a resposta, ou None
def envia(ip, porta, msg):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect((ip, porta))
    s.sendall((json.dumps(msg) + "\n").encode())
    # print("enviei pra", ip, porta, ":", msg)
    resposta = le_linha(s)
    s.close()
    if resposta is None:
        return None
    return json.loads(resposta)


# le bytes do socket ate achar o \n que marca o fim da mensagem
def le_linha(sock):
    dados = b""
    while b"\n" not in dados:
        parte = sock.recv(1024)
        if not parte:
            break
        dados += parte
    if not dados:
        return None
    linha = dados.split(b"\n")[0]
    return linha.decode()


# responde usando a conexao que ja ta aberta
def responde(conexao, msg):
    conexao.sendall((json.dumps(msg) + "\n").encode())


# pega o ip da maquina (truque de abrir socket pro 8.8.8.8 e ver com q ip o SO saiu)
def meu_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    s.close()
    return ip
