import tkinter as tk
from tkinter import simpledialog, messagebox
import socket
import threading
import time
import sys
import json
import rede
import coordenador

# endereco fixo do servico de nomes. em rede de verdade, troque pelo ip da maquina
# que roda o servico_nomes.py (ou passe como argumento na linha de comando).
IP_NOMES = "127.0.0.1"
PORTA_NOMES = 6000

T = 3   # de quanto em quanto tempo o cliente confere se o coordenador esta vivo


class Cliente:
    def __init__(self):
        self.ip = rede.meu_ip()
        self.porta = None              # porta do meu proprio servidor (definida ao iniciar)
        self.nome_quadro = None
        self.coord_ip = None
        self.coord_porta = None
        self.coordenador = None        # objeto Coordenador, se for eu quem hospeda o quadro
        self.objetos = []              # copia local do quadro, pra desenhar na tela
        self.participantes = []        # copia local de quem esta no quadro (uso na eleicao)
        self.selecionado = None        # id do objeto que EU selecionei
        self.cor_atual = "red"
        self.modo = None               # "linha", "quadrado" ou "selecionar"
        self.pontos = []               # pontos clicados esperando virar linha/quadrado
        self.precisa_redesenhar = False
        self.no_quadro = False
        self.em_eleicao = False
        self.trava = threading.Lock()

        self._inicia_servidor()
        threading.Thread(target=self._laco_ping, daemon=True).start()

        self.root = tk.Tk()
        self.root.title("Quadro Distribuido (SDWB)")
        self.root.protocol("WM_DELETE_WINDOW", self.fechar)
        self.tela_menu()
        self.root.after(100, self.checa_redesenho)
        self.root.mainloop()

    # ---------- telas ----------

    def limpa_tela(self):
        for w in self.root.winfo_children():
            w.destroy()

    def tela_menu(self):
        self.limpa_tela()
        self.no_quadro = False
        tk.Label(self.root, text="Quadro Distribuido (SDWB)", font=("Arial", 16)).pack(pady=15)
        tk.Button(self.root, text="Criar novo quadro", width=28, command=self.criar_quadro).pack(pady=5)
        tk.Button(self.root, text="Ingressar em quadro existente", width=28, command=self.ingressar).pack(pady=5)
        tk.Button(self.root, text="Sair", width=28, command=self.fechar).pack(pady=5)

    def tela_quadro(self):
        self.limpa_tela()
        topo = tk.Frame(self.root)
        topo.pack(side=tk.TOP, fill=tk.X)
        tk.Button(topo, text="Linha", command=self.modo_linha).pack(side=tk.LEFT)
        tk.Button(topo, text="Quadrado", command=self.modo_quadrado).pack(side=tk.LEFT)
        tk.Button(topo, text="Selecionar", command=self.modo_selecionar).pack(side=tk.LEFT)
        tk.Button(topo, text="Vermelho", command=lambda: self.escolhe_cor("red")).pack(side=tk.LEFT)
        tk.Button(topo, text="Azul", command=lambda: self.escolhe_cor("blue")).pack(side=tk.LEFT)
        tk.Button(topo, text="Colorir", command=self.colorir).pack(side=tk.LEFT)
        tk.Button(topo, text="Remover", command=self.remover).pack(side=tk.LEFT)
        tk.Button(topo, text="Sair do quadro", command=self.sair_do_quadro).pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(self.root, width=700, height=500, bg="white")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.clique_canvas)
        self.item2obj = {}        # item do canvas -> id do objeto (pra saber o que foi clicado)
        self.redesenha()

    # ---------- criar / ingressar ----------

    def criar_quadro(self):
        nome = simpledialog.askstring("Novo quadro", "Nome do quadro:")
        if not nome:
            return
        self.nome_quadro = nome
        self.objetos = []
        self.participantes = []
        # subo o coordenador aqui mesmo, no meu processo
        self.coordenador = coordenador.Coordenador(nome, self.ip, 0)
        self.coordenador.iniciar()
        self.coord_ip = self.ip
        self.coord_porta = self.coordenador.porta
        # registro o quadro nas paginas amarelas
        try:
            rede.envia(IP_NOMES, PORTA_NOMES,
                       {"op": "registrar", "nome": nome, "ip": self.ip, "porta": self.coordenador.porta})
        except:
            messagebox.showerror("Erro", "Nao consegui falar com o servico de nomes")
            return
        # entro no meu proprio quadro como um cliente normal
        self._entra_no_quadro()

    def ingressar(self):
        try:
            resp = rede.envia(IP_NOMES, PORTA_NOMES, {"op": "listar"})
        except:
            messagebox.showerror("Erro", "Nao consegui falar com o servico de nomes")
            return
        quadros = resp["quadros"]
        if not quadros:
            messagebox.showinfo("Quadros", "Nenhum quadro disponivel")
            return
        # janelinha pra escolher um quadro da lista
        janela = tk.Toplevel(self.root)
        janela.title("Quadros disponiveis")
        lista = tk.Listbox(janela, width=45)
        for q in quadros:
            lista.insert(tk.END, q[0] + "   (" + q[1] + ":" + str(q[2]) + ")")
        lista.pack(padx=10, pady=10)

        def escolher():
            sel = lista.curselection()
            if not sel:
                return
            q = quadros[sel[0]]
            self.nome_quadro = q[0]
            self.coord_ip = q[1]
            self.coord_porta = q[2]
            janela.destroy()
            self._entra_no_quadro()

        tk.Button(janela, text="Entrar", command=escolher).pack(pady=5)

    def _entra_no_quadro(self):
        try:
            estado = rede.envia(self.coord_ip, self.coord_porta,
                                {"op": "ingressar", "ip": self.ip, "porta": self.porta})
        except:
            messagebox.showerror("Erro", "Nao consegui falar com o coordenador")
            return
        self.objetos = estado["objetos"]
        self.participantes = estado["participantes"]
        self.no_quadro = True
        self.tela_quadro()

    def sair_do_quadro(self):
        self.liberar_selecao()
        sozinho = len(self.participantes) <= 1
        sou_host = (self.coordenador is not None and
                    self.coord_ip == self.ip and self.coord_porta == self.coordenador.porta)
        # aviso o coordenador que estou saindo
        try:
            rede.envia(self.coord_ip, self.coord_porta,
                       {"op": "sair", "ip": self.ip, "porta": self.porta})
        except:
            pass
        # se eu era o unico no quadro e ainda hospedo o coordenador, o quadro acaba
        if sou_host and sozinho:
            self.coordenador.parar()
            self.coordenador = None
            try:
                rede.envia(IP_NOMES, PORTA_NOMES, {"op": "remover", "nome": self.nome_quadro})
            except:
                pass
        # se sou host mas ainda tem gente, o coordenador continua rodando no meu processo
        self.no_quadro = False
        self.objetos = []
        self.selecionado = None
        self.tela_menu()

    def fechar(self):
        if self.coordenador is not None:
            self.coordenador.parar()
        self.root.destroy()

    # ---------- botoes do quadro ----------

    def modo_linha(self):
        self.liberar_selecao()
        self.modo = "linha"
        self.pontos = []

    def modo_quadrado(self):
        self.liberar_selecao()
        self.modo = "quadrado"
        self.pontos = []

    def modo_selecionar(self):
        self.modo = "selecionar"
        self.pontos = []

    def escolhe_cor(self, cor):
        self.cor_atual = cor

    def liberar_selecao(self):
        if self.selecionado is not None:
            try:
                rede.envia(self.coord_ip, self.coord_porta,
                           {"op": "liberar", "ip": self.ip, "porta": self.porta})
            except:
                pass
            self.selecionado = None
            self.precisa_redesenhar = True

    def colorir(self):
        if self.selecionado is None:
            messagebox.showinfo("Colorir", "Selecione um objeto primeiro")
            return
        try:
            resp = rede.envia(self.coord_ip, self.coord_porta,
                              {"op": "colorir", "obj_id": self.selecionado,
                               "cor": self.cor_atual, "ip": self.ip, "porta": self.porta})
        except:
            messagebox.showerror("Erro", "Nao consegui falar com o coordenador")
            return
        if resp.get("ok"):
            self.selecionado = None
        else:
            messagebox.showerror("Erro", resp.get("erro", "nao deu pra colorir"))

    def remover(self):
        if self.selecionado is None:
            messagebox.showinfo("Remover", "Selecione um objeto primeiro")
            return
        try:
            resp = rede.envia(self.coord_ip, self.coord_porta,
                              {"op": "remover", "obj_id": self.selecionado,
                               "ip": self.ip, "porta": self.porta})
        except:
            messagebox.showerror("Erro", "Nao consegui falar com o coordenador")
            return
        if resp.get("ok"):
            self.selecionado = None
        else:
            messagebox.showerror("Erro", resp.get("erro", "nao deu pra remover"))

    # ---------- desenho no canvas ----------

    def clique_canvas(self, evento):
        x, y = evento.x, evento.y
        if self.modo == "linha" or self.modo == "quadrado":
            self.pontos.append((x, y))
            if len(self.pontos) == 2:
                p1 = self.pontos[0]
                p2 = self.pontos[1]
                op = "add_linha" if self.modo == "linha" else "add_quadrado"
                try:
                    rede.envia(self.coord_ip, self.coord_porta,
                               {"op": op, "x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                except:
                    messagebox.showerror("Erro", "Nao consegui falar com o coordenador")
                self.pontos = []
        elif self.modo == "selecionar":
            oid = self.objeto_no_ponto(x, y)
            if oid is None:
                return
            try:
                resp = rede.envia(self.coord_ip, self.coord_porta,
                                  {"op": "selecionar", "obj_id": oid, "ip": self.ip, "porta": self.porta})
            except:
                messagebox.showerror("Erro", "Nao consegui falar com o coordenador")
                return
            if resp.get("ok"):
                self.selecionado = oid
                self.precisa_redesenhar = True
            else:
                messagebox.showerror("Erro", resp.get("erro", "nao deu pra selecionar"))

    def objeto_no_ponto(self, x, y):
        itens = self.canvas.find_closest(x, y)
        if not itens:
            return None
        return self.item2obj.get(itens[0])

    def redesenha(self):
        self.canvas.delete("all")
        self.item2obj = {}
        for obj in list(self.objetos):
            largura = 1
            if obj["id"] == self.selecionado:
                largura = 3   # deixa mais grosso o objeto que eu selecionei
            if obj["tipo"] == "linha":
                item = self.canvas.create_line(obj["x1"], obj["y1"], obj["x2"], obj["y2"],
                                               fill=obj["cor"], width=largura)
            else:
                item = self.canvas.create_rectangle(obj["x1"], obj["y1"], obj["x2"], obj["y2"],
                                                    outline=obj["cor"], width=largura)
            self.item2obj[item] = obj["id"]

    def checa_redesenho(self):
        # roda no fluxo principal do tkinter de tempos em tempos pra refletir o que
        # chegou pela rede (que vem em outra thread)
        if self.precisa_redesenhar and self.no_quadro:
            self.precisa_redesenhar = False
            try:
                self.redesenha()
            except:
                pass
        self.root.after(100, self.checa_redesenho)

    # ---------- meu servidor (recebe do coordenador e dos outros clientes) ----------

    def _inicia_servidor(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))                 # porta 0 = deixa o SO escolher uma livre
        self.porta = s.getsockname()[1]
        s.listen()
        self.servidor = s
        threading.Thread(target=self._laco_servidor, daemon=True).start()

    def _laco_servidor(self):
        while True:
            try:
                conexao, end = self.servidor.accept()
            except:
                break
            threading.Thread(target=self._trata_servidor, args=(conexao,), daemon=True).start()

    def _trata_servidor(self, conexao):
        linha = rede.le_linha(conexao)
        if linha is None:
            conexao.close()
            return
        msg = json.loads(linha)
        op = msg["op"]

        if op == "novo_objeto":
            self.objetos.append(msg["objeto"])
            self.precisa_redesenhar = True
            rede.responde(conexao, {"ok": True})

        elif op == "obj_colorido":
            for obj in self.objetos:
                if obj["id"] == msg["obj_id"]:
                    obj["cor"] = msg["cor"]
            self.precisa_redesenhar = True
            rede.responde(conexao, {"ok": True})

        elif op == "obj_removido":
            self.objetos = [o for o in self.objetos if o["id"] != msg["obj_id"]]
            if self.selecionado == msg["obj_id"]:
                self.selecionado = None
            self.precisa_redesenhar = True
            rede.responde(conexao, {"ok": True})

        elif op == "participantes":
            self.participantes = msg["lista"]
            rede.responde(conexao, {"ok": True})

        elif op == "ping":
            rede.responde(conexao, {"ok": True})

        elif op == "eleicao":
            # alguem comecou uma eleicao; respondo que estou vivo e comeco a minha
            rede.responde(conexao, {"ok": True})
            threading.Thread(target=self.inicia_eleicao, daemon=True).start()

        elif op == "coordenador":
            # me avisaram quem e o novo coordenador
            self.coord_ip = msg["ip"]
            self.coord_porta = msg["porta"]
            with self.trava:
                self.em_eleicao = False
            rede.responde(conexao, {"ok": True})

        conexao.close()

    # ---------- heartbeat e eleicao ----------

    def _laco_ping(self):
        falhas = 0
        while True:
            time.sleep(T)
            if not self.no_quadro:
                continue
            # se o coordenador sou eu mesmo, nao preciso me pingar
            if (self.coordenador is not None and
                    self.coord_ip == self.ip and self.coord_porta == self.coordenador.porta):
                falhas = 0
                continue
            try:
                rede.envia(self.coord_ip, self.coord_porta, {"op": "ping"})
                falhas = 0
            except:
                falhas += 1
                if falhas >= 2:     # ~2T sem resposta: coordenador caiu
                    falhas = 0
                    self.inicia_eleicao()

    # algoritmo do valentao: quem tem a maior porta entre os vivos vira coordenador
    def inicia_eleicao(self):
        with self.trava:
            if self.em_eleicao:
                return
            self.em_eleicao = True
        print("coordenador caiu, iniciando eleicao")
        algum_maior = False
        for p in list(self.participantes):
            if p[1] > self.porta and p != [self.ip, self.porta]:
                try:
                    resp = rede.envia(p[0], p[1], {"op": "eleicao"})
                    if resp and resp.get("ok"):
                        algum_maior = True
                except:
                    pass    # esse nao respondeu, segue
        if algum_maior:
            # tem alguem maior vivo; ele assume e me avisa pelo "coordenador"
            with self.trava:
                self.em_eleicao = False
            return
        self.vira_coordenador()

    def vira_coordenador(self):
        # vejo quem ainda esta vivo (tira o coordenador que caiu da lista)
        vivos = []
        for p in list(self.participantes):
            if p == [self.ip, self.porta]:
                vivos.append(p)
                continue
            try:
                rede.envia(p[0], p[1], {"op": "ping"})
                vivos.append(p)
            except:
                pass
        self.participantes = vivos

        # subo um coordenador novo no meu processo com o estado que eu ja tinha espelhado
        self.coordenador = coordenador.Coordenador(self.nome_quadro, self.ip, 0,
                                                   objetos=list(self.objetos),
                                                   participantes=list(vivos))
        self.coordenador.iniciar()
        self.coord_ip = self.ip
        self.coord_porta = self.coordenador.porta

        # atualizo o servico de nomes com o meu endereco de coordenador
        try:
            rede.envia(IP_NOMES, PORTA_NOMES,
                       {"op": "registrar", "nome": self.nome_quadro,
                        "ip": self.ip, "porta": self.coordenador.porta})
        except:
            pass

        # aviso todo mundo que agora o coordenador sou eu
        for p in vivos:
            if p == [self.ip, self.porta]:
                continue
            try:
                rede.envia(p[0], p[1], {"op": "coordenador", "ip": self.ip, "porta": self.coordenador.porta})
                rede.envia(p[0], p[1], {"op": "participantes", "lista": vivos})
            except:
                pass

        with self.trava:
            self.em_eleicao = False
        print("assumi como coordenador do quadro", self.nome_quadro)


if __name__ == "__main__":
    # da pra passar o ip do servico de nomes na linha de comando: python cliente.py 192.168.0.10
    if len(sys.argv) > 1:
        IP_NOMES = sys.argv[1]
    Cliente()
