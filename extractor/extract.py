import io
import logging
import os
from tempfile import NamedTemporaryFile



import re
from shutil import copyfile
import subprocess
from lxml.etree import parse, HTMLParser

local = False
import os



def get_subprocess_output(cmdline, redirect_stderr=True, display_output_on_exception=True, logger=None, **kwargs):
    if redirect_stderr: kwargs['stderr'] = subprocess.STDOUT

    try:
        output = subprocess.check_output(cmdline, **kwargs)
        if logger: logger.debug('Subprocess {} complete. Output is "{}".'.format(cmdline, output))

        return output

    except subprocess.CalledProcessError as e:
        if display_output_on_exception and logger:
            logger.exception('Subprocess {} returned {} : {}'.format(e.cmd, e.returncode, e.output.decode('ascii', errors='ignore')))

        raise
    return ''


LAMBDA_TASK_ROOT = os.environ.get('LAMBDA_TASK_ROOT', os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(LAMBDA_TASK_ROOT,'extractor', 'bin')

LIB_DIR = os.path.join(LAMBDA_TASK_ROOT,'extractor', 'lib')
LD_LIBRARY_PATH = os.path.join(LIB_DIR, 'pdftotext')

if local :
    BIN_DIR = ""
    LIB_DIR = "/usr/lib"
    LD_LIBRARY_PATH = None


logging.basicConfig(format='%(asctime)-15s [%(name)s-%(process)d] %(levelname)s: %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)



def _get_subprocess_output(*args, **kwargs):
    global logger
    kwargs['logger'] = logger
    return get_subprocess_output(*args, **kwargs)


class Extractor:

    txt_ = ""
    xml_ = ""
    @staticmethod
    def cmd(cmd_txt, local=True):
        if not local:
            return cmd_txt
        return os.path.join(BIN_DIR, cmd_txt)

    @staticmethod
    def pdf_to_text(document_path ):
        with NamedTemporaryFile("wb",suffix='.txt') as f:
            text_path = f.name
        cmd_ = [Extractor.cmd('pdftotext'), '-layout', '-nopgbrk', '-eol', 'unix', document_path, text_path]
        if not LD_LIBRARY_PATH:
            _get_subprocess_output(cmd_, shell=False)
        else:
            _get_subprocess_output(cmd_, shell=False, env=dict(LD_LIBRARY_PATH=LD_LIBRARY_PATH))

        with io.open(text_path, mode='r', encoding='utf-8', errors='ignore') as f:
            content= f.readlines()
            if len(content) < 20:
                return None
            result =[x.replace(u'\xa0', u'').replace(u'\xad', u'') for x in content if not re.match(r'^\s*$', x)]


        Extractor.txt_ = result
        return result

    @staticmethod
    def reduce_pages_10(pdf_info,input_file):

        for x in pdf_info.splitlines():
            x = str(x).replace("'",'')
            par = x.split(':', 1)[0].strip()
            val = x.split(':', 1)[1].strip()
            if "Pages" in par:
                if int(val) >=10:
                    cmd_ = [Extractor.cmd('cpdf'), input_file, "1-5",  "-o", input_file]
                    _get_subprocess_output(cmd_, shell=False)
                    break

    @staticmethod
    def reduce_pages_red(input_file,pdf_text):

        _text = [x.strip().lower().replace(" ","").replace(u'\xa0', u'').replace(u'\xad ', u'')
                         for x in pdf_text if not re.match(r'^\s*$', x)]

        if "thisisthediscountthatyouwillreceiveonyourbill" in "".join(_text):
            cmd_ = [Extractor.cmd('cpdf'), input_file, "1-2",  "-o", input_file]
            _get_subprocess_output(cmd_, shell=False)

    @staticmethod
    def get_pdf_info(document_path):
        cmd_ = [Extractor.cmd('pdfinfo'),  document_path]
        return _get_subprocess_output(cmd_, env=dict(LD_LIBRARY_PATH=os.path.join(LIB_DIR, 'pdftotext')))

    @staticmethod
    def _reduce_size_pdf(output_file, input_file,pdf_text):

        pdf_info = Extractor.get_pdf_info(input_file)
        Extractor.reduce_pages_10(pdf_info,input_file)
        Extractor.reduce_pages_red(input_file,pdf_text)
        if os.stat(input_file).st_size > 1000000:
            ## _get_subprocess_output(['ps2pdf', input_file, output_file],shell=False)
            _get_subprocess_output([Extractor.cmd('cpdf'), "-clean", "-draft", input_file, "-o", output_file],shell=False)
            if os.stat(output_file).st_size > 1000000:
                output_file1 = "/tmp/toto.pdf"
                _get_subprocess_output([Extractor.cmd('cpdf'), "-clean", "-draft", input_file, "-o", output_file1],shell=False)
                cmd2 = f"""gs -o {output_file} -sDEVICE=pdfwrite -c "/setrgbcolor {{pop pop pop 0 setgray}} bind def"  -f {output_file1}"""
                _get_subprocess_output(cmd2, shell=True)
        else:
            copyfile(input_file, output_file)

    @staticmethod
    def tet_convert(document_path):
        with NamedTemporaryFile(suffix='.xml', delete=False) as f:
            xml_path = f.name
        cmd_ = [Extractor.cmd('tet'), "-m", "page", "--docopt",
         "engines={noimage notextcolor} tetml={elements={nometadata}}",
         "--pageopt", "vectoranalysis={structures=tables}","-o",xml_path, document_path]
        _get_subprocess_output(cmd_, shell=False)
        Extractor.xml_data= parse(xml_path, HTMLParser()).getroot()
        Extractor.xml_= parse(xml_path).getroot()
        os.remove(xml_path)

    @staticmethod
    def extract(document_path):
        pdf_text= Extractor.txt_
        if not Extractor.txt_:
            pdf_text =  Extractor.pdf_to_text(document_path)
        if pdf_text :
            with NamedTemporaryFile("wb", delete=False) as shrunk:
                shrunk_file_path = shrunk.name
            Extractor._reduce_size_pdf(shrunk_file_path, document_path,pdf_text)
            Extractor.tet_convert(shrunk_file_path)
            os.remove(shrunk_file_path)

    @staticmethod
    def process_pdf(document_path):

        try:
            result = Extractor.extract(document_path)
        except Exception as e:

            result = dict(success=False, reason=str(e))
        finally:
            os.remove(document_path)
            return result

    @staticmethod
    def check_bill(document_path):

        is_bill = False
        embedded = ["resiembeddednetwork",'ocenergy.com.au','winconnect.com.au']
        bad_message = "Sorry we could not automatically read your bill.\nCan you please make sure you have an original PDF and then try again."
        gas_message = "Sorry, we do not yet cover gas bills.\nCan you please make sure you have an original electricity PDF and then try again."
        installment_message = """This is an installment bill that do not contain your consumption profile.\n
                              Can you please make sure you have an electricity bill with consumption rates and volumes and then try again."""

        try:
            pdf_text =  Extractor.pdf_to_text(document_path)
            if not pdf_text:
                return False, f"This is a scanned bill.\n{bad_message}"
            else:
                for x in pdf_text:
                    if "mirn" in x.lower() or "dpi" in x.lower():
                        return False, gas_message
                    if "nmi" in x.lower() or "meter identifier" in x.lower():
                        is_bill = True
                        break
                    if "Instalment Bill" in x:
                        return False, installment_message
                    if any(y in x.lower().replace(" ","") for y in embedded):
                        is_bill =True
                        break
            if not is_bill:
                return is_bill, f"This is not a valid bill.\n{bad_message}"
            return is_bill,x
        except Exception as e:
            print(e)
            return False, f"This is not a valid pdf.\n{bad_message}"






if __name__ =="__main__":
    file_name = "/home/amine/Downloads/bougeois_elysain_bill.pdf"

    flag, res =  Extractor.check_bill(file_name)

    print(flag, res)











