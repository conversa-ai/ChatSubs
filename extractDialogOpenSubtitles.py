import os
import gzip
import pandas as pd
from datetime import datetime, timedelta
import json
import re
import argparse
import multiprocessing as mp
from pathlib import Path


processed_languages = ['spa', 'cat', 'baq', 'glg']
timeThreshold = timedelta(seconds=1)

# reg expr
HTML_TAGS = re.compile('<.*?>')
MULTIPLE_CLRF = re.compile('[\n\r]+')
DIALOG_CONTINUATION_SUSPENSIVE_POINTS = re.compile('\.\.\.[\n\r]+\.\.\.')
DIALOG_CONTINUATION = re.compile('(w+)[\n\r]+(w+)')
BEGIN_DIALOG1 = re.compile(r'^\s*-+\s*')
BEGIN_DIALOG2 = re.compile(r'\n\s*-+\s*')
MULTI_SPACES = re.compile('[ \t]+')

#debug = False
debug = False


def is_time_line(line):
    # 00:00:40,560 --> 00:00:42,994
    # TODO test reg_expr = '\d+:\d+:\d+,\d+[ \t]+\-\->[ \t]+\d+:\d+:\d+,\d+'
    reg_expr = '\d+:\d+:\d+'
    m = re.search(reg_expr, line)
    if m:
        return True
    else:
        return False


def ignore_line(line):
    if len(line) == 0:
        return False
    reg_expr = '^\d+$'
    m = re.search(reg_expr, line)
    if m:
        return True
    else:
        return False


def get_time_limits(line):
    time_init = time_end = None

    # 00:00:40,560 --> 00:00:42,994
    reg_expr = '(\d+:\d+:\d+,\d+)[ ]+-->[ ]+(\d+:\d+:\d+,\d+)'
    m = re.search(reg_expr, line)
    if m:
        if m.group(1):
            time_init = m.group(1)
        if m.group(2):
            time_end = m.group(2)

    return time_init, time_end


def get_time_difference(init_time, end_time):
    try:
        # Convert time strings to datetime objects
        init_time_datetime = datetime.strptime(init_time, '%H:%M:%S,%f')
        end_time_datetime = datetime.strptime(end_time, '%H:%M:%S,%f')

        # Calculate time difference
        time_diff = end_time_datetime - init_time_datetime
    except Exception:
        return timeThreshold

    return time_diff


def clean_textual_line_breaks(text):
    # TODO incluir excepcion " para uds.\nyo se eso pero tengo"
    punctuation = ".!?"
    lines = text.splitlines()
    try:
        new_lines = []
        for i, line in enumerate(lines):
            if i > 0 and line[0] != " " and lines[i - 1][-1] not in punctuation:
                new_lines[-1] += " " + line.strip()
            else:
                new_lines.append(line.strip())
        text = "\n".join(new_lines)
    except Exception:
        pass
    return text


def clean_dialog_segment(dialog_segment):
    punctuation = ".!?"
    dialog_segment = re.sub(HTML_TAGS, '', dialog_segment)
    dialog_segment = re.sub(MULTIPLE_CLRF, '\n', dialog_segment)
    dialog_segment = re.sub(DIALOG_CONTINUATION_SUSPENSIVE_POINTS, " ", dialog_segment)
    dialog_segment = dialog_segment.replace("...", ".")
    dialog_segment = clean_textual_line_breaks(dialog_segment)
    dialog_segment = dialog_segment.replace(' , ', ', ')
    dialog_segment = re.sub(MULTI_SPACES, ' ', dialog_segment)
    dialog_segment = re.sub(BEGIN_DIALOG1, '', dialog_segment)
    dialog_segment = re.sub(BEGIN_DIALOG2, '\n', dialog_segment)
    #dialog_segment = dialog_segment.strip().capitalize()
    if len(dialog_segment) > 0 and dialog_segment[-1] not in punctuation:
        dialog_segment += '.'
    return dialog_segment


def extract_dialog(content):
    output = []
    lines = content.splitlines()
    last_time = '00:00:00,000'  # init time

    dialog_segment = ""
    cnt_segment = 0
    for line in lines:
        if is_time_line(line):
            init_time, end_time = get_time_limits(line)
            if init_time is None or end_time is None:
                continue

            if get_time_difference(last_time, init_time) > timeThreshold:
                if len(dialog_segment) > 0:
                    dialog_segment = clean_dialog_segment(dialog_segment)
                    # clean isolated lines of text, no dialog
                    if dialog_segment.count('\n') == 0:
                        dialog_segment = ""
                        continue
                    output.append(dialog_segment)

                    if debug:
                        print(f'--- BEGIN cnt_segment {cnt_segment}: ---\n' + dialog_segment + f'\n--- END cnt_segment {cnt_segment} ---\n')

                dialog_segment = ""
                cnt_segment += 1
            last_time = end_time
        else:
            if ignore_line(line):
                continue
            else:
                if len(line) > 0:
                    dialog_segment += line + '\n'

    print(f'Num segments: {cnt_segment}')
    return output


def accept_file(file_name):
    try:
        subtitle_matadata = metadata_df[metadata_df["IDSubtitleFile"] == int(file_name)]
    except:
        return False

    sub_language_id = subtitle_matadata["SubLanguageID"].item()
    if sub_language_id in processed_languages:
        return True
    else:
        return False


def generate_processing_gzip_file_list(directory, list_gzip):
    for filename in os.listdir(directory):
        path = os.path.join(directory, filename)
        if os.path.isdir(path):
            generate_processing_gzip_file_list(path, list_gzip)
        elif path.endswith('.gz'):
            file_name = os.path.splitext(os.path.basename(path))[0]
            if accept_file(file_name):
                list_gzip.append(path)


def test_valid_final_clause(output):
    # * título de traducción: ej. "Traduccion zacado-2007-". Al final del subtitulado
    # * autor subtítulos: ej. "Subtítulos por aRGENTeaM"
    if 'traduccion' in output.lower():
        return False
    elif 'subtítulos' in output.lower():
        return False
    elif 'subtitulos' in output.lower():
        return False
    return True


def delete_final_clause(output):
    if not test_valid_final_clause(output[-1]):
        del output[-1]
    return output


def process_gzip_file(filepath):
    with gzip.open(filepath, 'rt', encoding="ISO-8859-1") as f:
        print('\nProcessing: ' + filepath)
        output = extract_dialog(f.read())

    if output:
        if len(output) > 0:
            output = delete_final_clause(output)

        dest_dir = os.path.join(output_folder, os.path.relpath(Path(filepath).parent, Path(input_folder)))
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        final_output = {
            'dialogues': output,
        }

        file_name = os.path.splitext(os.path.basename(filepath))[0] + '.jsonl'
        with open(os.path.join(dest_dir, file_name), "w", encoding='utf-8') as output_file:
            print('writing: ' + str(dest_dir) + '/' + file_name)
            output_file.write(json.dumps(final_output))


def init_worker(inputfolder, outputfolder):
    global input_folder, output_folder
    output_folder = outputfolder
    input_folder = inputfolder


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputfolder', type=str, required=True)
    parser.add_argument('--outputfolder', type=str, required=True)
    parser.add_argument('--lang', type=str, required=True)
    args = parser.parse_args()

    # read metadata
    global metadata_df
    global output_folder, input_folder

    print('reading metadata...')
    metadata_df = pd.read_csv(os.path.join(args.inputfolder, 'export.txt'), delimiter='\t', header=2)
    output_folder = args.outputfolder
    input_folder = args.inputfolder

    # process open subtitle files
    print('generating processing file list...')
    gzip_file_list = []
    generate_processing_gzip_file_list(args.inputfolder, gzip_file_list)

    # num_process = 1
    num_process = mp.cpu_count() - 1
    print(f'processing subtitle files (num_process: {num_process}) ...')

    with mp.Pool(num_process, initializer=init_worker, initargs=(input_folder, output_folder,)) as p:
        p.map(process_gzip_file, gzip_file_list)
