import re
import json


class SyntaxHiglighting():
    ''' Abstract datatype that holds color values '''
    def __init__(self, foreground: str, background: str,
                 comment: str, selection: str, number: str,
                 string: str, variable: str):
        self.foreground = foreground
        self.background = background
        self.comment = comment
        self.selection = selection
        self.number = number
        self.string = string
        self.variable = variable


def _stripcomments(text):
    ''' Removes c-styled comments '''
    return re.sub('//.*?\n|/\*.*?\*/', '', text, flags=re.S)


def parse_vscode_theme(theme: str) -> SyntaxHiglighting:
    ''' Parses vscode themes '''
    if theme[-5:] == '.json':
        with open(theme) as file:
            raw_file = file.read()
        cleaned_file = _stripcomments(raw_file)
        parsed_json = json.loads(cleaned_file)
        foreground = parsed_json['colors']['editor.foreground']
        background = parsed_json['colors']['editor.background']
        selection = parsed_json['colors']['editor.selectionBackground']
        for dictionary in parsed_json['tokenColors']:
            for key in dictionary:
                if dictionary[key] == 'Comment':
                    comment = dictionary['settings']['foreground']
                elif dictionary[key] == 'Number':
                    number = dictionary['settings']['foreground']
                elif dictionary[key] == 'String':
                    string = dictionary['settings']['foreground']
                elif dictionary[key] == 'Variable':
                    variable = dictionary['settings']['foreground']

        s = SyntaxHiglighting(foreground, background, comment,
                              selection, number, string,
                              variable)
        return s
    else:
        print(f'{theme} is not a json file')
