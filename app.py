import os
import requests
import re
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.units import inch
# Removido: import PyPDF2
# Removido: import docx
# Removido: import io

app = Flask(__name__)

# --- Configurações de Produção ---
# ATENÇÃO: Em produção, a SECRET_KEY DEVE ser lida de uma variável de ambiente.
# Se esta variável não for definida na VPS, a aplicação NÃO INICIARÁ.
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY')

# --- Configurações da API Gemini ---
# Sua chave de API real do Google Gemini.
# ATENÇÃO: Em produção, a API_KEY DEVE ser lida de uma variável de ambiente.
# Se esta variável não for definida na VPS, a aplicação NÃO INICIARÁ.
API_KEY = os.environ.get('GEMINI_API_KEY')
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro-latest:generateContent?key={API_KEY}"

# Inicializa o SocketIO. Em produção, ajuste 'cors_allowed_origins' para seu domínio.
# Para testes, '*' permite qualquer origem, mas não é seguro para produção.
socketio = SocketIO(app, cors_allowed_origins="*") 

# --- REGRAS DE REVISÃO (PROMPT) - FORNECIDAS PELO USUÁRIO ---
REGRAS_REVISAO = """
🎯 PROMPT MESTRE V4.0 - AUDITOR DE REMISSÕES LEGISLATIVAS
[INÍCIO DO PROMPT MESTRE]

PERSONA E DIRETRIZ PRIMÁRIA

Você atuará como um Auditor Jurídico Especializado em Remissões Legislativas. Seu papel é o de um Professor Catedrático e parecerista de alto nível, com domínio absoluto de legislação brasileira, técnicas de auditoria normativa e atualização jurídica. Seu foco exclusivo será auditar todas as remissões legislativas contidas em um material jurídico (artigo, parecer, aula, capítulo etc.).
Seu público-alvo são operadores do direito e pesquisadores de pós-graduação, com máxima exigência por precisão normativa e rigor técnico.
A superficialidade ou a presença de remissões incorretas, desatualizadas ou irrelevantes é considerada uma falha crítica.

METODOLOGIA DE AUDITORIA: ANÁLISE DE REMISSÕES LEGISLATIVAS
🎓 Sua Missão:
Auditar exclusivamente as remissões a normas jurídicas (leis, artigos, incisos, parágrafos, códigos, CF/88, etc.). Para cada remissão identificada no texto, verifique e avalie os seguintes critérios:
Existência e Exatidão

A norma citada existe com a redação exata apresentada?

O número do artigo, inciso, alínea ou parágrafo está correto?

O nome da norma (ex: “Lei nº 8.666/1993”) está escrito corretamente?

Vigência e Atualização

A norma citada está em vigor ou foi revogada, alterada ou modificada?

Caso tenha havido revogação ou alteração, identifique a norma superveniente que modificou o dispositivo citado.

Citações a normas revogadas ou obsoletas devem ser classificadas como ERRO CRÍTICO.

Pertinência e Relevância Jurídica

A remissão é pertinente ao ponto jurídico abordado? Ela sustenta com solidez o argumento proposto?

Existe norma mais específica, adequada ou hierarquicamente superior para embasar melhor o raciocínio apresentado?


📑 SUA ENTREGA: RELATÓRIO TÉCNICO DE AUDITORIA DE REMISSÕES
Apresente um relatório detalhado com o título:
Relatório de Auditoria de Remissões Legislativas
Para cada remissão identificada, apresente uma ficha no seguinte formato:
Trecho Original: [copiar o trecho com a remissão]

Status da Remissão: [OK] / [DESATUALIZADA] / [IMPRECISA] / [IRRELEVANTE]

Correção Sugerida: [se aplicável, indique a remissão corrigida, norma vigente ou ajuste textual]

Classificação: [INFORMATIVO], [RELEVANTE] ou [CRÍTICO]

Comentário Técnico: [breve explicação com embasamento jurídico]


INSTRUÇÃO DE EXECUÇÃO
Após eu colar o texto completo do material jurídico a ser auditado, inicie imediatamente a auditoria das remissões legislativas, apresentando o relatório conforme descrito acima.
[FIM DO PROMPT MESTRE]
"""

# --- Função para dividir o texto em blocos ---
def dividir_texto(texto, max_caracteres=3500):
    """
    Divide um texto longo em blocos menores para processamento,
    tentando quebrar em pontos de pontuação para manter o contexto.
    """
    blocos = []
    inicio = 0
    while inicio < len(texto):
        # Define o fim potencial do bloco
        fim = min(inicio + max_caracteres, len(texto))

        # Se o fim do bloco for o final do texto, adiciona e sai
        if fim == len(texto):
            blocos.append(texto[inicio:])
            break

        # Tenta encontrar um ponto de quebra (ponto final, interrogação, exclamação)
        # dentro de uma margem segura antes do limite máximo de caracteres.
        # Busca 50 caracteres para trás a partir do fim para encontrar uma pontuação.
        quebra_candidata = texto.rfind('.', inicio, fim)
        if quebra_candidata == -1: # Se não encontrar ponto, tenta outras pontuações
            quebra_candidata = texto.rfind('?', inicio, fim)
        if quebra_candidata == -1:
            quebra_candidata = texto.rfind('!', inicio, fim)
        
        # Se encontrou um ponto de quebra válido, usa ele
        if (quebra_candidata != -1 and quebra_candidata >= inicio):
            # Garante que a quebra não seja muito próxima do início do bloco
            if (quebra_candidata - inicio) > (max_caracteres * 0.5) or (fim - quebra_candidata) < 50:
                bloco_atual = texto[inicio:quebra_candidata + 1].strip()
                if bloco_atual: # Adiciona apenas se não for vazio
                    blocos.append(bloco_atual)
                inicio = quebra_candidata + 1
                continue # Continua para a próxima iteração do loop

        # Se não encontrou um ponto de quebra ideal, ou se a quebra é muito no início do bloco,
        # faz uma quebra "bruta" no limite máximo de caracteres, mas tenta evitar cortar palavras.
        # Procura por um espaço ou quebra de linha perto do limite.
        espaco_de_quebra = texto.rfind(' ', inicio, fim)
        if espaco_de_quebra != -1 and espaco_de_quebra > inicio:
            bloco_atual = texto[inicio:espaco_de_quebra].strip()
            if bloco_atual:
                blocos.append(bloco_atual)
            inicio = espaco_de_quebra + 1
        else:
            # Último recurso: quebra no limite exato de caracteres
            bloco_atual = texto[inicio:fim].strip()
            if bloco_atual:
                blocos.append(bloco_atual)
            inicio = fim
            
    # Filtra blocos vazios que podem surgir de quebras indesejadas
    return [b for b in blocos if b.strip()]

# Removido: Função para extrair texto de PDF
# Removido: Função para extrair texto de DOCX

# --- Função para converter Markdown-like bold para HTML para Reportlab ---
def converter_markdown_para_html_reportlab(text):
    """
    Converte **texto** para <b>texto</b> e escapa caracteres HTML especiais.
    """
    # Primeiro, escapa caracteres HTML especiais para evitar conflitos
    # com as tags que vamos adicionar.
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    
    # Converte **texto** para <b>texto</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    
    return text

# --- Função para chamar a API Gemini ---
# Agora recebe socketio para emitir progresso
def revisar_bloco_com_gemini(bloco_de_texto, current_block, total_blocks, sid):
    """
    Envia um bloco de texto para a API Gemini para revisão e retorna o texto revisado.
    Emite atualizações de progresso via SocketIO.
    """
    # Verifica se a API_KEY está definida antes de fazer a chamada
    if not API_KEY:
        error_msg = "Erro: API_KEY não configurada. Por favor, defina a variável de ambiente GEMINI_API_KEY."
        print(error_msg)
        socketio.emit('error_message', {'message': error_msg}, room=sid)
        return None

    full_prompt = f"{REGRAS_REVISAO}\n\nTexto para auditoria:\n{bloco_de_texto}"
    
    payload = {
        "contents": [
            {"parts": [{"text": full_prompt}]}
        ]
    }
    
    headers = {
        "Content-Type": "application/json"
    }

    retries = 3
    for i in range(retries):
        try:
            socketio.emit('progress_update', {'message': f'Auditando bloco {current_block}/{total_blocks}... (Tentativa {i+1})'}, room=sid)
            response = requests.post(API_URL, headers=headers, json=payload, timeout=120) 
            response.raise_for_status()
            
            candidates = response.json().get('candidates', [])
            if candidates and candidates[0].get('content'):
                parts = candidates[0]['content'].get('parts', [])
                if parts and parts[0].get('text'):
                    return parts[0]['text']
            return None
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Erro na tentativa {i+1}/{retries} ao chamar a API para o bloco {current_block}: {e}"
            print(error_msg)
            socketio.emit('progress_update', {'message': error_msg}, room=sid)
            if i < retries - 1:
                import time
                time.sleep(2 ** i + 1) 
            else:
                return None

# --- Rota principal para a página inicial ---
@app.route('/')
def index():
    return render_template('index.html')

# --- Evento SocketIO para iniciar a revisão ---
@socketio.on('start_revision')
def handle_start_revision(data):
    sid = request.sid # ID da sessão do cliente SocketIO
    socketio.emit('progress_update', {'message': 'Iniciando auditoria legislativa...'}, room=sid)
    
    texto_para_auditoria = ""
    nome_base_arquivo = "relatorio_auditoria_legislativa" # Nome padrão
    
    try:
        # Removida a lógica de verificação e extração de arquivos
        
        # Pega o texto da textarea
        if data.get('text_content'):
            texto_para_auditoria = data['text_content']

        if not texto_para_auditoria.strip():
            socketio.emit('error_message', {'message': 'Por favor, cole um texto para revisão.'}, room=sid)
            return

        blocos_originais = dividir_texto(texto_para_auditoria)
        total_blocos = len(blocos_originais)
        blocos_revisados = []
        
        for i, bloco in enumerate(blocos_originais):
            revisao = revisar_bloco_com_gemini(bloco, i + 1, total_blocos, sid)
            if revisao:
                blocos_revisados.append(revisao)
            else:
                blocos_revisados.append(f"\n[ERRO NA AUDITORIA DO BLOCO {i+1}: Original:\n{bloco}\n]")
                socketio.emit('progress_update', {'message': f'ATENÇÃO: Falha na auditoria do bloco {i+1}. Inserindo marcador de erro.'}, room=sid)
        
        texto_final_revisado = "".join(blocos_revisados)
        
        # --- Converte o negrito Markdown para HTML para o PDF ---
        texto_final_revisado_formatado = converter_markdown_para_html_reportlab(texto_final_revisado)

        socketio.emit('progress_update', {'message': 'Auditoria concluída. Gerando PDF... (Isso pode levar alguns segundos)'}, room=sid)

        # --- Geração do PDF ---
        pdf_filename = f"{nome_base_arquivo}_revisao_legislativa.pdf"
        
        # Cria o PDF em memória para enviar via WebSocket
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name='Justify', parent=styles['Normal'], alignment=TA_JUSTIFY))
        styles.add(ParagraphStyle(name='CustomTitle', parent=styles['Title'], fontSize=24, spaceAfter=20, alignment=TA_CENTER))
        styles.add(ParagraphStyle(name='SubHeading', parent=styles['h2'], fontSize=16, spaceAfter=10, textColor='#333333', alignment=TA_LEFT))
        styles.add(ParagraphStyle(name='ReportSection', parent=styles['h3'], fontSize=14, spaceAfter=8, textColor='#0056b3', alignment=TA_LEFT))
        styles.add(ParagraphStyle(name='Preformatted', parent=styles['Normal'], fontName='Courier', fontSize=10, backColor='#f0f0f0', borderPadding=(5,5,5,5)))

        flowables = []
        flowables.append(Paragraph("Relatório de Auditoria de Remissões Legislativas", styles['CustomTitle']))
        flowables.append(Spacer(1, 0.3 * inch))
        flowables.append(Paragraph("Gerado por Revisor de Texto com Gemini", styles['SubHeading']))
        flowables.append(Spacer(1, 0.5 * inch))

        flowables.append(Paragraph("<h3>Relatório de Auditoria:</h3>", styles['ReportSection']))
        for linha in texto_final_revisado_formatado.split('\n'):
            if linha.strip():
                flowables.append(Paragraph(linha, styles['Normal']))
                flowables.append(Spacer(1, 0.05 * inch))

        doc.build(flowables)
        buffer.seek(0) # Volta para o início do buffer
        
        # Envia o PDF como array de bytes para o frontend
        socketio.emit('download_ready', {
            'filename': pdf_filename,
            'file_data': list(buffer.getvalue()), # Converte bytes para lista de ints para JSON
            'mimetype': 'application/pdf'
        }, room=sid)
        
        print("PDF gerado e enviado com sucesso via WebSocket!")

    except Exception as e:
        error_message = f"Ocorreu um erro inesperado durante a auditoria: {e}"
        print(error_message)
        socketio.emit('error_message', {'message': error_message}, room=sid)

# --- Eventos de conexão/desconexão (opcional, para depuração) ---
@socketio.on('connect')
def test_connect():
    print(f'Cliente conectado: {request.sid}')
    emit('progress_update', {'message': 'Conectado ao servidor.'})

@socketio.on('disconnect')
def test_disconnect():
    print(f'Cliente desconectado: {request.sid}')

# --- Executa a aplicação Flask com SocketIO ---
if __name__ == '__main__':
    # Em produção, debug=False. host='0.0.0.0' permite acesso externo.
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)
