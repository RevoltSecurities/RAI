# This are Utilis Class for fixing new lines in description and instruction while saving in yaml file

class MultilineStr(str):
    pass

def multiline_str_representer(dumper, data):
    if '\n' in data:
        cleaned_lines = [line.rstrip() for line in data.split('\n')]
        cleaned_data = '\n'.join(cleaned_lines)
        return dumper.represent_scalar('tag:yaml.org,2002:str', cleaned_data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)