'''
This file defines the command-line interface for the AHK frontend
to call the Python backend.  The general call format is:

 > python3 backend_cli.py <function_name> <function_parameters...> <profile_name (if required)>

Return values of functions will be placed in the file "backend_cli.output",
formatted as specified by the frontend developer.  (Note: make sure
calls to the cli are synchronous if return values are to be used,
so you ensure data is written before you try to read it.)

Some functions do not require a profile_name parameter, specifically those which
do not interact with the filter in any way (for now, this is simply the setters and getters
for profile names).

Scroll down to DelegateFunctionCall() to see documentation of all supported functions.
   
The input and output filter filepaths are specified in config.py.
(Eventually these will be the same, but for testing they're distinct.)

Error handling:  if anything goes wrong while the python script is executing,
all relevant error messages will be written to "backend_cli.log", since the
standard output will not be available when the scripts are run via AHK.
For example, running the following command:
 > python3 backend_cli.py adjust_currency_tier "Chromatic Orb" a
will generate the error message:
"ValueError: invalid literal for int() with base 10: 'a'",
which will be logged to the log file along with the stack trace.

Testing feature:
 - Insert "TEST" as the first argument after "python3 backend_cli.py" to run tests
 - This wil write output to test output filter, rather than the PathOfExile filter path
 - This will also save all profile updates in a separate testing profile so as to not ruin
   one's real profile(s).  Used in all test_suite.py calls.
'''

from collections import OrderedDict
import os
from pathlib import Path
import shlex
import shutil
import sys
import traceback
from typing import List, Tuple

import consts
import file_helper
import logger
from loot_filter import InputFilterSource, LootFilter
from loot_filter_rule import RuleVisibility
import profile
from type_checker import CheckType

kLogFilename = 'backend_cli.log'
kInputFilename = 'backend_cli.input'
kOutputFilename = 'backend_cli.output'
kExitCodeFilename = 'backend_cli.exit_code'

# Map of function name -> dictionary of properties indexed by the following string keywords:
# - NumParamsOptions: List[int] (excludes function name and profile param)
# - HasProfileParam: bool
# - ModifiesFilter: bool
# - NumParamsForMatch: int, only used for functions that modify the filter. It
#   Tells how many parameters need to be the same for two functions of this name to be
#   reducible to a single function in the profile changes file.  For example:
#     > adjust_currency_tier "Chromatic Orb" +1
#     > adjust_currency_tier "Chromatic Orb" +1
#   is reducible to
#     > adjust_currency_tier "Chromatic Orb" +2
#   so this value would be 1 for 'adjust_currency_tier'.
kFunctionInfoMap = {
    # Profiles
    'is_first_launch' : {
        'NumParamsOptions' : [0],
        'HasProfileParam' : False,
        'ModifiesFilter' : False,
    },
    'get_all_profile_names' : {
        'NumParamsOptions' : [0],
        'HasProfileParam' : False,
        'ModifiesFilter' : False,
    },
    'create_new_profile' : {
        'NumParamsOptions' : [1],
        'HasProfileParam' : False,
        'ModifiesFilter' : False,
    },
    'rename_profile' : {
        'NumParamsOptions' : [2],
        'HasProfileParam' : False,
        'ModifiesFilter' : False,
    },
    'delete_profile' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : False,
        'ModifiesFilter' : False,
    },
    'set_active_profile' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : False,
        'ModifiesFilter' : False,
    },
    # Check Filters Exist
    'check_filters_exist' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Import and Reload
    # These are *not* considered as mutator functions,
    # because they do not contribute to Profile.changes.
    'import_downloaded_filter' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'load_input_filter' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Miscellaneous
    'run_batch' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'get_rule_matching_item' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'set_rule_visibility' : { 
        'NumParamsOptions' : [3],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 2,
    },
    # Currency
    'set_currency_to_tier' : { 
        'NumParamsOptions' : [2],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 1,
    },
    'get_tier_of_currency' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'get_all_currency_tiers' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'set_currency_tier_min_visible_stack_size' : {
        'NumParamsOptions' : [2],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 1,
    },
    'get_currency_tier_min_visible_stack_size' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Essences
    'get_all_essence_tier_visibilities' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'set_hide_essences_above_tier' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_hide_essences_above_tier' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Divination Cards
    'get_all_div_card_tier_visibilities' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'set_hide_div_cards_above_tier' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_hide_div_cards_above_tier' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Unique Items
    'get_all_unique_item_tier_visibilities' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'set_hide_unique_items_above_tier' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_hide_unique_items_above_tier' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Unique Maps
    'get_all_unique_map_tier_visibilities' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'set_hide_unique_maps_above_tier' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_hide_unique_maps_above_tier' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Oils
    'set_lowest_visible_oil' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_lowest_visible_oil' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Quality Gems
    'set_gem_min_quality' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_gem_min_quality' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Quality Flasks
    'set_flask_min_quality' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_flask_min_quality' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Hide Maps Below Tier
    'set_hide_maps_below_tier' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_hide_maps_below_tier' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Generic BaseTypes
    'set_basetype_visibility' : { 
        'NumParamsOptions' : [2, 3],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 2,
    },
    'get_basetype_visibility' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'get_all_visible_basetypes' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Flasks Types
    'set_flask_visibility' : { 
        'NumParamsOptions' : [2, 3],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 2,
    },
    'get_flask_visibility' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'get_all_visible_flasks' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Socket Rules
    'add_remove_socket_rule' : { 
        'NumParamsOptions' : [2, 3],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 2,
    },
    'get_all_added_socket_rules' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # RGB Items
    'set_rgb_item_max_size' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 0,
    },
    'get_rgb_item_max_size' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    # Chaos Recipe
    'set_chaos_recipe_enabled_for' : { 
        'NumParamsOptions' : [2],
        'HasProfileParam' : True,
        'ModifiesFilter' : True,
        'NumParamsForMatch' : 1,
    },
    'is_chaos_recipe_enabled_for' : { 
        'NumParamsOptions' : [1],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
    'get_all_chaos_recipe_statuses' : { 
        'NumParamsOptions' : [0],
        'HasProfileParam' : True,
        'ModifiesFilter' : False,
    },
}

def Error(e):
    logger.Log('Error ' + str(e))
    raise RuntimeError(e)
# End Error

def FileExists(path: str) -> bool:
    return Path(path).is_file()
# End FileExists

def CheckNumParams(params_list: List[str], required_num_params: int):
    CheckType(params_list, 'params_list', list)
    CheckType(required_num_params, 'required_num_params', int)
    if (len(params_list) != required_num_params):
        error_message: str = ('incorrect number of parameters given for '
                              'function {}: required {}, got {}').format(
                                   sys.argv[1], required_num_params, len(params_list))
        Error(error_message)
# End CheckNumParams

# Encloses the string in double quotes if it contains a space or single quote,
# otherwise just returns the given string.  Note: does not check for double quotes in string.
def QuoteStringIfRequired(input_string: str) -> str:
    CheckType(input_string, 'input_string', str)
    if ((" " in input_string) or ("'" in input_string)):
        return '"' + input_string + '"'
    return input_string
# End QuoteStringIfRequired

# Given a list of strings, joins the strings with a space,
# and additionally encloses any string that contains a single quote or space in ""
def JoinParams(params_list: List[str]) -> str:
    CheckType(params_list, 'params_list', list, str)
    return ' '.join(QuoteStringIfRequired(param) for param in params_list)
# End JoinParams

def WriteOutput(output_string: str):
    CheckType(output_string, 'output_string', str)
    with open(kOutputFilename, 'w') as output_file:
        output_file.write(output_string)
# End WriteOutput

# function_output_string should be the whole string containing the given function call's output
def AppendFunctionOutput(function_output_string: str):
    CheckType(function_output_string, 'function_output_string', str)
    with open(kOutputFilename, 'a') as output_file:
        output_file.write(function_output_string + '\n@\n')
# End AppendFunctionOutput

# Note: changes_dict is a chain of ordered dicts
def AddFunctionToChangesDict(function_tokens: List[str], changes_dict: OrderedDict):
    CheckType(function_tokens, 'function_tokens', list, str)
    CheckType(changes_dict, 'changes_dict', OrderedDict)
    function_name = function_tokens[0]
    num_params_for_match = kFunctionInfoMap[function_name]['NumParamsForMatch']
    current_dict = changes_dict
    # "+ 1" here because the first token is the function name, not a param
    for i in range(num_params_for_match + 1):
        current_token = function_tokens[i]
        if (i == num_params_for_match):
            current_dict[current_token] = function_tokens[i + 1]
        else:
            if (current_token not in current_dict):
                current_dict[current_token] = OrderedDict()
            current_dict = current_dict[current_token]
# End AddFunctionToChangesDict

# Returns list of lists of function tokens, for example:
# [['adjust_currency_tier', 'Chromatic Orb', '1'],
#  ['hide_uniques_above_tier', '3']]
def ConvertChangesDictToFunctionListRec(changes_dict: OrderedDict or str,
                                        current_prefix_list: List[str] = []) -> List[List[str]]:
    CheckType(changes_dict, 'changes_dict', (OrderedDict, str))
    CheckType(current_prefix_list, 'current_prefix_list', list, str)
    # changes_dict may just be final parameter, in which case it's a string
    if (isinstance(changes_dict, str)):
        last_param: str = changes_dict
        return [current_prefix_list + [last_param]]
    # Otherwise, recursively handle all keys in changes_dict
    result_list = []
    for param, subdict in changes_dict.items():
        result_list.extend(
                ConvertChangesDictToFunctionListRec(subdict, current_prefix_list + [param]))
    return result_list
# End ConvertChangesDictToFunctionListRec

def ConvertChangesDictToFunctionList(changes_dict: OrderedDict) -> List[str]:
    CheckType(changes_dict, 'changes_dict', OrderedDict)
    # Get list of lists of function tokens from recursive function above
    token_lists = ConvertChangesDictToFunctionListRec(changes_dict)
    function_list = []
    for token_list in token_lists:
        function_list.append(JoinParams(token_list))
    return function_list
# End ConvertChangesDictToFunctionList

def UpdateProfileChangesFile(changes_fullpath: str,
                             new_function_name: str,
                             new_function_params: List[str]):
    CheckType(changes_fullpath, 'changes_fullpath', str)
    CheckType(new_function_name, 'new_function_name', str)
    CheckType(new_function_params, 'new_function_params', list, str)
    # Parse changes file as chain of OrderedDicts:
    # function_name -> param1 -> param2 -> ... -> last_param
    #  > set_currency_tier "Chaos Orb" 3
    # 'set_currency_tier' -> 'Chaos Orb' -> '3':
    # {'set_currency_tier' : {'Chaos Orb' : '3'}}
    parsed_changes_dict = OrderedDict()
    changes_lines_list = file_helper.ReadFile(changes_fullpath)
    for line in changes_lines_list:
        tokens_list = shlex.split(line.strip())
        AddFunctionToChangesDict(tokens_list, parsed_changes_dict)
    # Now check if new function matches with any functions in parsed_changes_dict
    num_params_for_match = kFunctionInfoMap[new_function_name]['NumParamsForMatch']
    match_flag: bool = new_function_name in parsed_changes_dict
    matched_rule_tokens_list = [new_function_name]
    if (match_flag):
        current_dict = parsed_changes_dict[new_function_name]
        for i in range(num_params_for_match):
            current_param = new_function_params[i]
            if (current_param not in current_dict):
                match_flag = False
                break
            else:
                matched_rule_tokens_list.append(current_param)
                current_dict = current_dict[current_param]
        # Append last parameter if we found a match
        # A bit wacky, but current_dict is just last parameter here due to the last line above
        if (match_flag):
            matched_rule_tokens_list.append(current_dict)
    # If we found a match, we update the matched function in the changes_dict,
    # combining matching functions by simply overwriting the last parameter
    # If we didn't find a match, we instead just add the new function to our changes_dict
    # Either way, the following line of code does exactly what we want:
    AddFunctionToChangesDict([new_function_name] + new_function_params, parsed_changes_dict)
    # Convert our parsed_changes_dict into a list of functions
    changes_list = ConvertChangesDictToFunctionList(parsed_changes_dict)
    # Write updated profile changes
    file_helper.WriteToFile(changes_list, changes_fullpath)
# End UpdateProfileChangesFile

# Note: loot_filter is None iff the command does not have a profile name parameter.
def DelegateFunctionCall(loot_filter: LootFilter or None,
                         function_name: str,
                         function_params: List[str],
                         *,  # require subsequent arguments to be named in function call
                         in_batch: bool = False,
                         suppress_output: bool = False):
    CheckType(loot_filter, 'loot_filter', (LootFilter, type(None)))
    CheckType(function_name, 'function_name', str)
    CheckType(function_params, 'function_params_list', list)
    CheckType(in_batch, 'in_batch', bool)
    CheckType(suppress_output, 'suppress_output', bool)
    # Alias config_values for convenience
    config_values = loot_filter.profile_obj.config_values if loot_filter else None
    # 
    output_string = ''
    # ================================== Check Filters Exist ==================================
    if (function_name == 'check_filters_exist'):
        '''
        check_filters_exist
         - Outputs a sequence of integer flags (1 or 0), one per line, indicating whether
           each of the following filters exist:
            - Downloaded filter
            - Input filter
            - Output filter
         - Example: > python3 backend_cli.py check_filters_exist MyProfile
        '''
        CheckNumParams(function_params, 0)
        downloaded_filter_exists = os.path.isfile(config_values['DownloadedLootFilterFullpath'])
        input_filter_exists = os.path.isfile(config_values['InputLootFilterFullpath'])
        output_filter_exists = os.path.isfile(config_values['OutputLootFilterFullpath'])
        output_string += '\n'.join(str(int(flag)) for flag in
                (downloaded_filter_exists, input_filter_exists, output_filter_exists))
    # ================================= Import / Reload Filter =================================
    elif ((function_name in ('import_downloaded_filter', 'load_input_filter')) and not in_batch):
        '''
        import_downloaded_filter
         - Copies or moves the downloaded filter to the input directory (depending on profile
           config), parses the input filter, adds DLF-generated rules, applies profile changes,
           and writes the final result to the output filter
         - Output: None
         - Example: > python3 backend_cli.py import_downloaded_filter MyProfile
         
        load_input_filter
         - Parses the input filter, adds DLF-generated rules, applies profile changes,
           and writes the final result to the output filter
         - Output: None
         - Example: > python3 backend_cli.py load_input_filter MyProfile
        '''
        CheckNumParams(function_params, 0)
        changes_lines: List[str] = file_helper.ReadFile(config_values['ChangesFullpath'])
        # Changes are applied in backend_cli rather than within the LootFilter class,
        # because they are formatted as backend_cli calls within the Profile.changes file.
        for function_call_string in changes_lines:
            _function_name, *_function_params = shlex.split(function_call_string)
            DelegateFunctionCall(loot_filter, _function_name, _function_params,
                                 in_batch = True, suppress_output = True)
        loot_filter.SaveToFile()
    # ======================================= Run Batch =======================================
    elif ((function_name == 'run_batch') and not in_batch):
        '''
        run_batch
         - Runs the batch of functions specified in file backend_cli.input
         - Format is one function call per line, given as: <function_name> <function_params...>
         - Output: concatenation of the outputs of all the functions, with each function output
           separated by the line containing the single character: `@`
         - Example: > python3 run_batch MyProfile
        '''
        CheckNumParams(function_params, 0)
        WriteOutput('')  # clear the output file, since we will be appending output in batch
        contains_mutator = False
        function_call_list: List[str] = file_helper.ReadFile(kInputFilename)
        for function_call_string in function_call_list:
            if (function_call_string.strip() == ''):
                continue
            # need different variable names here to not overwrite the existing ones
            _function_name, *_function_params = shlex.split(function_call_string)
            if (kFunctionInfoMap[_function_name]['ModifiesFilter']):
                contains_mutator = True
            DelegateFunctionCall(loot_filter, _function_name, _function_params,
                                 in_batch = True, suppress_output = False)
        # Check if batch contained a mutator and save filter if so
        if (contains_mutator):
            loot_filter.SaveToFile()
    # ========================================== Profile ==========================================
    elif (function_name == 'is_first_launch'):
        '''
        is_first_launch
         - Output: "1" if this is the first launch of the program (i.e. requires setup),
           "0" otherwise
         - It is considered first launch iff there are no profiles
         - Example: > python3 backend_cli.py is_first_launch
        '''
        CheckNumParams(function_params, 0)
        profile_names_list = profile.GetAllProfileNames()
        is_first_launch_flag: bool = (len(profile_names_list) == 0)
        output_string = str(int(is_first_launch_flag))
    elif (function_name == 'get_all_profile_names'):
        '''
        get_all_profile_names
         - Output: newline-separated list of all profile names, with currently active profile first
         - Example: > python3 backend_cli.py get_all_profile_names
        '''
        CheckNumParams(function_params, 0)
        profile_names_list = profile.GetAllProfileNames()
        output_string += '\n'.join(profile_names_list)
    elif (function_name == 'create_new_profile'):
        '''
        create_new_profile <new_profile_name>
         - Creates a new profile from the config values given in backend_cli.input
         - Each input line takes the form: "<keyword>:<value>", with keywords defined in profile.py
         - Required keywords: 'DownloadDirectory', 'PathOfExileDirectory', 'DownloadedLootFilterFilename'
         - Does nothing if a profile with the given new_profile_name already exists
         - Output: "1" if the new profile was created, "0" otherwise
         - Example: > python3 backend_cli.py create_new_profile MyProfile
        '''
        CheckNumParams(function_params, 1)
        new_profile_name = function_params[0]
        config_values: dict = file_helper.ReadFileToDict(kInputFilename)
        created_profile = profile.CreateNewProfile(new_profile_name, config_values)
        output_string += str(int(created_profile != None))
    elif (function_name == 'rename_profile'):
        '''
        rename_profile <original_profile_name> <new_profile_name>
         - Renames a profile, renaming all the corresponding config files,
         - Updates general.config if needed
         - Raises an error if the profile original_profile_name does not exist
         - Example: > python3 backend_cli.py rename_profile MyProfile MyFancyProfile
        '''
        CheckNumParams(function_params, 2)
        original_profile_name, new_profile_name = function_params
        profile.RenameProfile(original_profile_name, new_profile_name)
    elif (function_name == 'delete_profile'):
        '''
        delete_profile <profile_name>
         - Deletes the given profile, removing all corresponding config files
         - Updates general.config if needed
         - Raises an error if the profile profile_name does not exist
         - Example: > python3 backend_cli.py delete_profile MyProfile
        '''
        CheckNumParams(function_params, 1)
        profile_name = function_params[0]
        profile.DeleteProfile(profile_name)
    elif (function_name == 'set_active_profile'):
        '''
        set_active_profile <new_active_profile_name>
         - Raises an error if new_active_profile_name does not exist
         - Output: None
         - Example: > python3 backend_cli.py set_active_profile MyFancyProfile
        '''
        CheckNumParams(function_params, 1)
        profile.SetActiveProfile(function_params[0])
    # ====================================== Rule Matching ======================================
    elif (function_name == 'get_rule_matching_item'):
        '''
        TODO: this is probably broken with refactor.
        get_rule_matching_item
         - Takes an item text as input in backend_cli.input
         - Finds the rule in the PoE filter matching the item and writes it to backend_cli.output
         - The first two lines of output will be `type_tag:<type_tag>` and `tier_tag:<tier_tag>`,
           these two tags together form a unique key for the rule
         - Ignores rules with AreaLevel conditions, as well as many other niche keywords
         - Socket rules only implemented as numeric counting for now, ignores color requirements
         - Example: > python3 backend_cli.py get_rule_matching_item MyProfile
        '''
        CheckNumParams(function_params, 0)
        item_text_lines: List[str] = file_helper.ReadFile(kInputFilename)
        type_tag, tier_tag = loot_filter.GetRuleMatchingItem(item_text_lines)
        output_string = 'type_tag:{}\ntier_tag:{}\n'.format(str(type_tag), str(tier_tag))
        if ((type_tag != None) and (tier_tag != None)):
            matched_rule = loot_filter.GetRule(type_tag, tier_tag)
            output_string += '\n'.join(matched_rule.text_lines)        
    elif (function_name == 'set_rule_visibility'):
        '''
        set_rule_visibility <type_tag: str> <tier_tag: str> <visibility: {show, hide, disable}>
         - Shows, hides, or disables the rule specified by the given type and tier tags
         - The visibility parameter is one of: `show`, `hide`, `disable`
         - Output: None (could output rule_found_flag if needed)
         - Example > python3 backend_cli.py set_rule_visibility "rare->redeemer" t12 show
         - Note: quotes (either type) are necessary for tags containing a ">" character,
           since the shell will normally iterpret as the output redirection signal
         - Example: > python3 backend_cli.py set_rule_visibility uniques 5link disable MyProfile
        '''
        CheckNumParams(function_params, 3)
        type_tag, tier_tag, visibility_string = function_params
        visibility_map = {
                'show': RuleVisibility.kShow,
                'hide': RuleVisibility.kHide,
                'disable': RuleVisibility.kDisabledAny}
        loot_filter.GetRule(type_tag, tier_tag).SetVisibility(visibility_map[visibility_string])
    # ======================================== Currency ========================================
    elif (function_name == 'set_currency_to_tier'):
        '''
        set_currency_to_tier <currency_name: str> <tier: int>
         - Moves the given currency type to the specified tier for all unstacked and stacked rules
         - Output: None
         - Example: > python3 backend_cli.py set_currency_to_tier "Chromatic Orb" 5 MyProfile
        '''
        CheckNumParams(function_params, 2)
        currency_name: str = function_params[0]
        target_tier: int = int(function_params[1])
        loot_filter.SetCurrencyToTier(currency_name, target_tier)
    elif (function_name == 'get_tier_of_currency'):
        '''
        get_tier_of_currency <currency_name: str>
         - Output: tier (int) containing the given currency type
         - Example: > python3 backend_cli.py get_tier_of_currency "Chromatic Orb" MyProfile
        '''
        CheckNumParams(function_params, 1)
        currency_name: str = function_params[0]
        output_string = str(loot_filter.GetTierOfCurrency(currency_name))
    elif (function_name == 'get_all_currency_tiers'):
        '''
        get_all_currency_tiers
         - Output: newline-separated sequence of `<currency_name: str>;<tier: int>`
         - Example: > python3 backend_cli.py get_all_currency_tiers MyProfile
        '''
        CheckNumParams(function_params, 0)
        for tier in range(1, consts.kNumCurrencyTiersExcludingScrolls + 1):
            currency_names = loot_filter.GetAllCurrencyInTier(tier)
            output_string += ''.join((currency_name + ';' + str(tier) + '\n')
                                        for currency_name in currency_names)
        if (output_string[-1] == '\n'): output_string = output_string[:-1]  # remove final newline
    elif (function_name == 'set_currency_tier_min_visible_stack_size'):
        '''
        set_currency_tier_min_visible_stack_size <tier: int or string> <stack_size: int or "hide_all">
         - Shows currency stacks >= stack_size and hides stacks < stack_size for the given tier
         - If stack_size is "hide_all", all currency of the given tier will be hidden
         - Valid stack_size values: {1, 2, 4} for tiers1-7, {1, 2, 4, 6} for tiers 8-9 and scrolls
         - tier is an integer [1-9] or "tportal"/"twisdom" for Portal/Wisdom Scrolls
         - Output: None
         - Example: > python3 backend_cli.py set_currency_min_visible_stack_size 7 6 MyProfile
         - Example: > python3 backend_cli.py set_currency_min_visible_stack_size twisdom hide_all MyProfile
        '''
        CheckNumParams(function_params, 2)
        tier_str: str = function_params[0]
        min_stack_size_str: str = function_params[1]
        loot_filter.SetCurrencyTierMinVisibleStackSize(tier_str, min_stack_size_str)
    elif (function_name == 'get_currency_tier_min_visible_stack_size'):
        '''
        get_currency_tier_min_visible_stack_size <tier: int or str>
         - "tier" is an int, or "tportal"/"twisdom" for portal/wisdom scrolls
         - Output: min visible stack size for the given currency tier
         - Example: > python3 backend_cli.py get_currency_tier_min_visible_stack_size 4 MyProfile
         - Example: > python3 backend_cli.py get_currency_tier_min_visible_stack_size twisdom MyProfile
        '''
        CheckNumParams(function_params, 1)
        tier_str: str = function_params[0]
        output_string = str(loot_filter.GetCurrencyTierMinVisibleStackSize(tier_str))
    # ========================================= Essences =========================================
    elif (function_name == 'get_all_essence_tier_visibilities'):
        '''
        get_all_essence_tier_visibilities
         - Output: newline-separated sequence of `<tier>;<visible_flag>`, one per tier
         - <tier> is an integer representing the tier, <visibile_flag> is 1/0 for True/False
         - Example: > python3 backend_cli.py get_all_essence_tier_visibilities MyProfile
        '''
        CheckNumParams(function_params, 0)
        for tier in range(1, consts.kNumEssenceTiers + 1):
            output_string += str(tier) + ';' + str(int(
                    loot_filter.GetEssenceTierVisibility(tier) == RuleVisibility.kShow)) + '\n'
        if (output_string[-1] == '\n'): output_string = output_string[:-1]  # remove final newline
    elif (function_name == 'set_hide_essences_above_tier'):
        '''
        set_hide_essences_above_tier <tier: int>
         - Sets the essence tier "above" which all will be hidden
           (higher essence tiers are worse)
         - Output: None
         - Example: > python3 backend_cli.py set_hide_essences_above_tier 3 MyProfile
        '''
        CheckNumParams(function_params, 1)
        max_visible_tier: int = int(function_params[0])
        loot_filter.SetHideEssencesAboveTierTier(max_visible_tier)
    elif (function_name == 'get_hide_essences_above_tier'):
        '''
        get_hide_essences_above_tier
         - Output: single integer, the tier above which all essences are hidden
         - Example: > python3 backend_cli.py get_hide_essences_above_tier MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetHideEssencesAboveTierTier())
    # ========================================= Div Cards =========================================
    elif (function_name == 'get_all_div_card_tier_visibilities'):
        '''
        get_all_div_card_tier_visibilities
         - Output: newline-separated sequence of `<tier>;<visible_flag>`, one per tier
         - <tier> is an integer representing the tier, <visibile_flag> is 1/0 for True/False
         - Example: > python3 backend_cli.py get_all_div_card_tier_visibilities MyProfile
        '''
        CheckNumParams(function_params, 0)
        for tier in range(1, consts.kNumDivCardTiers + 1):
            output_string += str(tier) + ';' + str(int(
                    loot_filter.GetDivCardTierVisibility(tier) == RuleVisibility.kShow)) + '\n'
        if (output_string[-1] == '\n'): output_string = output_string[:-1]  # remove final newline
    elif (function_name == 'set_hide_div_cards_above_tier'):
        '''
        set_hide_div_cards_above_tier <tier: int>
         - Sets the essence tier "above" which all will be hidden
           (higher tiers are worse)
         - Output: None
         - Example: > python3 backend_cli.py set_hide_essences_above_tier 3 MyProfile
        '''
        CheckNumParams(function_params, 1)
        max_visible_tier: int = int(function_params[0])
        loot_filter.SetHideDivCardsAboveTierTier(max_visible_tier)
    elif (function_name == 'get_hide_div_cards_above_tier'):
        '''
        get_hide_div_cards_above_tier
         - Output: single integer, the tier above which all essences are hidden
         - Example: > python3 backend_cli.py get_hide_div_cards_above_tier MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetHideDivCardsAboveTierTier())
    # ======================================= Unique Items =======================================
    elif (function_name == 'get_all_unique_item_tier_visibilities'):
        '''
        get_all_unique_item_tier_visibilities
         - Output: newline-separated sequence of `<tier>;<visible_flag>`, one per tier
         - <tier> is an integer representing the tier, <visibile_flag> is 1/0 for True/False
         - Example: > python3 backend_cli.py get_all_unique_item_tier_visibilities MyProfile
        '''
        CheckNumParams(function_params, 0)
        for tier in range(1, consts.kNumUniqueItemTiers + 1):
            output_string += str(tier) + ';' + str(int(
                    loot_filter.GetUniqueItemTierVisibility(tier) == RuleVisibility.kShow)) + '\n'
        if (output_string[-1] == '\n'): output_string = output_string[:-1]  # remove final newline
    elif (function_name == 'set_hide_unique_items_above_tier'):
        '''
        set_hide_unique_items_above_tier <tier: int>
         - Sets the unique item tier "above" which all will be hidden
           (higher tiers are less valuable)
         - Output: None
         - Example: > python3 backend_cli.py set_hide_unique_items_above_tier 3 MyProfile
        '''
        CheckNumParams(function_params, 1)
        max_visible_tier: int = int(function_params[0])
        loot_filter.SetHideUniqueItemsAboveTierTier(max_visible_tier)
    elif (function_name == 'get_hide_unique_items_above_tier'):
        '''
        get_hide_unique_items_above_tier
         - Output: single integer, the tier above which all unique items are hidden
         - Example: > python3 backend_cli.py get_hide_unique_items_above_tier MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetHideUniqueItemsAboveTierTier())
    # ======================================= Unique Maps =======================================
    elif (function_name == 'get_all_unique_map_tier_visibilities'):
        '''
        get_all_unique_map_tier_visibilities
         - Output: newline-separated sequence of `<tier>;<visible_flag>`, one per tier
         - <tier> is an integer representing the tier, <visibile_flag> is 1/0 for True/False
         - Example: > python3 backend_cli.py get_all_unique_map_tier_visibilities MyProfile
        '''
        CheckNumParams(function_params, 0)
        for tier in range(1, consts.kNumUniqueMapTiers + 1):
            output_string += str(tier) + ';' + str(int(
                    loot_filter.GetUniqueMapTierVisibility(tier) == RuleVisibility.kShow)) + '\n'
        if (output_string[-1] == '\n'): output_string = output_string[:-1]  # remove final newline
    elif (function_name == 'set_hide_unique_maps_above_tier'):
        '''
        set_hide_unique_maps_above_tier <tier: int>
         - Sets the unique map tier "above" which all will be hidden
           (higher tiers are less valuable)
         - Output: None
         - Example: > python3 backend_cli.py set_hide_unique_maps_above_tier 3 MyProfile
        '''
        CheckNumParams(function_params, 1)
        max_visible_tier: int = int(function_params[0])
        loot_filter.SetHideUniqueMapsAboveTierTier(max_visible_tier)
    elif (function_name == 'get_hide_unique_maps_above_tier'):
        '''
        get_hide_unique_maps_above_tier
         - Output: single integer, the tier above which all unique maps are hidden
         - Example: > python3 backend_cli.py get_hide_unique_maps_above_tier MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetHideUniqueMapsAboveTierTier())
    # ======================================= Blight Oils =======================================
    elif (function_name == 'set_lowest_visible_oil'):
        '''
        set_lowest_visible_oil <oil_name: str>
         - Sets the lowest-value blight oil which to be shown
         - Output: None
         - Example: > python3 backend_cli.py set_lowest_visible_oil "Violet Oil" MyProfile
        '''
        CheckNumParams(function_params, 1)
        loot_filter.SetLowestVisibleOil(function_params[0])
    elif (function_name == 'get_lowest_visible_oil'):
        '''
        get_lowest_visible_oil
         - Output: the name of the lowest-value blight oil that is shown
         - Example: > python3 backend_cli.py get_lowest_visible_oil MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = loot_filter.GetLowestVisibleOil()
    # ======================================= Gem Quality =======================================
    elif (function_name == 'set_gem_min_quality'):
        '''
        set_gem_min_quality <quality: int in [1, 20]>
         - Sets the minimum quality below which gems will not be shown by gem quality rules
         - Output: None
         - Example: > python3 backend_cli.py set_gem_min_quality 10 MyProfile
        '''
        CheckNumParams(function_params, 1)
        min_quality: int = int(function_params[0])
        loot_filter.SetGemMinQuality(min_quality)
    elif (function_name == 'get_gem_min_quality'):
        '''
        get_gem_min_quality
         - Output: single integer, minimum shown gem quality for gem quality rules
         - Example: > python3 backend_cli.py get_gem_min_quality MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetGemMinQuality())
    # ====================================== Flask Quality ======================================
    elif (function_name == 'set_flask_min_quality'):
        '''
        set_flask_min_quality <quality: int in [1, 20]>
         - Sets the minimum quality below which flasks will not be shown by flask quality rules
         - Output: None
         - Example: > python3 backend_cli.py set_flask_min_quality 14 MyProfile
        '''
        CheckNumParams(function_params, 1)
        min_quality: int = int(function_params[0])
        loot_filter.SetFlaskMinQuality(min_quality)
    elif (function_name == 'get_flask_min_quality'):
        '''
        get_flask_min_quality
         - Output: single integer, minimum shown flask quality for flask quality rules
         - Example: > python3 backend_cli.py get_flask_min_quality MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetFlaskMinQuality())
    # ========================================== Maps ==========================================
    elif (function_name == 'set_hide_maps_below_tier'):
        '''
        set_hide_maps_below_tier <tier: int>
         - Sets the map tier below which all will be hidden (use 0/1 to show all)
         - Output: None
         - Example: > python3 backend_cli.py set_hide_maps_below_tier 14 MyProfile
        '''
        CheckNumParams(function_params, 1)
        min_visibile_tier: int = int(function_params[0])
        loot_filter.SetHideMapsBelowTierTier(min_visibile_tier)
    elif (function_name == 'get_hide_maps_below_tier'):
        '''
        get_hide_maps_below_tier
         - Output:  single integer, the tier below which all maps are hidden
         - Example: > python3 backend_cli.py get_hide_maps_below_tier MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = str(loot_filter.GetHideMapsBelowTierTier())
    # ==================================== Generic BaseTypes ====================================
    elif (function_name == 'set_basetype_visibility'):
        '''
        set_basetype_visibility <base_type: str> <visibility_flag: int> <(optional) rare_only_flag: int>
         - This function never hides BaseTypes, it only modifies its own "Show" rule.
         - visibility_flag is 1 for True (visible), 0 for False (not included in DLF rule)
         - rare_only_flag is 1 for only rare items, 0 for any non-unique items
         - rare_only_flag should *only* be specified when visibility_flag is 1;
           when visibility_flag is 0, the base_type is removed from both rules.
         - Output: None
         - Example: > python3 backend_cli.py set_basetype_visibility "Hubris Circlet" 1 0 MyProfile
        '''
        if (len(function_params) == 2):
            function_params.append('0')
        CheckNumParams(function_params, 3)
        base_type: str = function_params[0]
        enable_flag: bool = bool(int(function_params[1]))
        rare_only_flag: bool = bool(int(function_params[2]))
        loot_filter.SetBaseTypeRuleEnabledFor(base_type, enable_flag, rare_only_flag)
    elif (function_name == 'get_basetype_visibility'):
        '''
        get_basetype_visibility <base_type: str>
         - rare_flag is 1 for rare items, 0 for any non-unique items
         - Output: a space-separated pair of boolean ints, e.g. "0 1"
            - The first value corresponds to any non-unique, the second corresponds to rares only
            - 1 indicates visible, 0 indicates not present in DLF rule
         - Example: > python3 backend_cli.py get_basetype_visibility "Hubris Circlet" MyProfile
        '''
        CheckNumParams(function_params, 1)
        base_type: str = function_params[0]
        any_visibility_flag = loot_filter.IsBaseTypeRuleEnabledFor(base_type, rare_flag=False)
        rare_visibility_flag = loot_filter.IsBaseTypeRuleEnabledFor(base_type, rare_flag=True)
        output_string = '{} {}'.format(int(any_visibility_flag), int(rare_visibility_flag))
    elif (function_name == 'get_all_visible_basetypes'):
        '''
        get_all_visible_basetypes
         - Output: newline-separated sequence of <base_type>;<rare_only_flag: int>
         - rare_only_flag is 1 for only rare items, 0 for any non-unique items
         - Example: > python3 backend_cli.py get_all_visible_basetypes MyProfile
        '''
        CheckNumParams(function_params, 0)
        visible_base_types_any = loot_filter.GetAllVisibleBaseTypes(rare_flag=False)
        visible_base_types_rare = loot_filter.GetAllVisibleBaseTypes(rare_flag=True)
        # Compute rare BaseTypes that are not also in any
        visible_base_types_rare_only = list(set(visible_base_types_rare) - set(visible_base_types_any))
        for base_type in visible_base_types_any:
            output_string += '{};0\n'.format(base_type)
        for base_type in visible_base_types_rare_only:
            output_string += '{};1\n'.format(base_type)
        if ((len(output_string) > 0) and (output_string[-1] == '\n')):
            output_string = output_string[:-1]  # remove final newline
    # ========================================= Flasks =========================================
    elif (function_name == 'set_flask_visibility'):
        '''
        set_flask_visibility <base_type: str> <visibility_flag: int> <(optional) high_ilvl_flag: int>
         - This does not overwrite any original filter rules, only adds a rule on top.
         - This function never hides flasks, it only modifies its own "Show" rule.
         - <base_type> is any valid flask BaseType
         - visibility_flag is 1 for True (visible), 0 for False (not included in DLF rule)
         - high_ilvl_flag is 1 for only high ilvl flasks, 0 for any flasks
         - rare_only_flag should *only* be specified when visibility_flag is 1;
           when visibility_flag is 0, the base_type is removed from both rules.
         - Output: None
         - Example: > python3 backend_cli.py set_flask_rule_enabled_for "Quartz Flask" 1 0 MyProfile
        '''
        if (len(function_params) == 2):
            function_params.append('0')
        CheckNumParams(function_params, 3)
        flask_base_type: str = function_params[0]
        enable_flag: bool = bool(int(function_params[1]))
        high_ilvl_flag: bool = bool(int(function_params[2]))
        loot_filter.SetFlaskRuleEnabledFor(flask_base_type, enable_flag, high_ilvl_flag)
    elif (function_name == 'get_flask_visibility'):
        '''
        get_flask_visibility <base_type: str>
         - <base_type> is any valid flask BaseType
         - Output: a space-separated pair of boolean ints, e.g. "0 1"
            - The first value corresponds to any ilvl, the second corresponds to high ilvl only
            - 1 indicates visible, 0 indicates not present in DLF rule
         - Example: > python3 backend_cli.py is_flask_rule_enabled_for "Quicksilver Flask" MyProfile
        '''
        CheckNumParams(function_params, 1)
        flask_base_type: str = function_params[0]
        any_visibility_flag = loot_filter.IsFlaskRuleEnabledFor(
                flask_base_type, high_ilvl_flag=False)
        high_ilvl_visibility_flag = loot_filter.IsFlaskRuleEnabledFor(
                flask_base_type, high_ilvl_flag=True)
        output_string = '{} {}'.format(int(any_visibility_flag), int(high_ilvl_visibility_flag))
    elif (function_name == 'get_all_visible_flasks'):
        '''
        get_all_visible_flasks
         - Output: newline-separated sequence of <flask_basetype>;<high_ilvl_flag: int>
         - high_ilvl_flag is 1 for only high ilvl flasks, 0 for any flasks
         - Example: > python3 backend_cli.py get_all_visible_flasks MyProfile
        '''
        CheckNumParams(function_params, 0)
        visible_flask_types_any_ilvl = loot_filter.GetAllVisibleFlaskTypes(high_ilvl_flag=False)
        visible_flask_types_high_ilvl = loot_filter.GetAllVisibleFlaskTypes(high_ilvl_flag=True)
        for flask_base_type in visible_flask_types_any_ilvl:
            output_string += '{};0\n'.format(flask_base_type)
        for flask_base_type in visible_flask_types_high_ilvl:
            output_string += '{};1\n'.format(flask_base_type)
        if ((len(output_string) > 0) and (output_string[-1] == '\n')):
            output_string = output_string[:-1]  # remove final newline
    # ========================================= Socket Rules =========================================
    elif (function_name == 'add_remove_socket_rule'):
        '''
        add_remove_socket_rule <socket_string: str> <(optional) item_slot: str> <add_flag: bool>
         - TODO: add socket_string explanation
         - <item_slot> is one of the following (case insensitive): "Weapons", "Body Armours",
           "Helmets", "Gloves", "Boots", "Amulets", "Rings", "Belts", or "Any"
         - add_flag is 1 to add a corresponding rule, 0 to remove the corresponding rule
         - Output: None
         - Example: > python3 backend_cli.py add_remove_socket_rule "B-B-G-X" 1 MyProfile
         - Example: > python3 backend_cli.py add_remove_socket_rule "b-b xx" 1 MyProfile
        '''
        if (len(function_params) == 2):
            function_params.insert(1, 'any')
        CheckNumParams(function_params, 3)
        socket_string, item_slot, add_flag_string = function_params
        add_flag: bool = bool(int(add_flag_string))
        if (add_flag):
            loot_filter.AddSocketRule(socket_string, item_slot)
        else:
            loot_filter.RemoveSocketRule(socket_string, item_slot)
    elif (function_name == 'get_all_added_socket_rules'):
        '''
        get_all_added_socket_rules
         - Output: newline-separated sequence of <socket_string>;<item_slot>
         - Example: > python3 backend_cli.py get_all_added_socket_rules MyProfile
        '''
        CheckNumParams(function_params, 0)
        lines = [';'.join(string_pair) for string_pair in loot_filter.GetAllAddedSocketRules()]
        output_string = '\n'.join(lines)
    # ======================================== Rgb Items ========================================
    elif (function_name == 'set_rgb_item_max_size'):
        '''
        set_rgb_item_max_size <size: {none, small, medium, large}>
         - Sets the maximum size at which an RGB item is shown
         - "small" = 4, "medium" = 6, "large" = 8
         - Output: None
         - Example: > python3 backend_cli.py set_rgb_item_max_size small MyProfile
        '''
        CheckNumParams(function_params, 1)
        rgb_item_max_size: str = function_params[0]
        loot_filter.SetRgbItemMaxSize(rgb_item_max_size)
    elif (function_name == 'get_rgb_item_max_size'):
        '''
        get_rgb_item_max_size
         - Output:  max-size of shown RGB items, one of {none, small, medium, large}
         - Example: > python3 backend_cli.py get_rgb_item_max_size MyProfile
        '''
        CheckNumParams(function_params, 0)
        output_string = loot_filter.GetRgbItemMaxSize()
    # =================================== Chaos Recipe Rares ===================================
    elif (function_name == 'set_chaos_recipe_enabled_for'):
        '''
        set_chaos_recipe_enabled_for <item_slot: str> <enable_flag: int>
         - <item_slot> is one of: "Weapons", "Body Armours", "Helmets", "Gloves",
           "Boots", "Amulets", "Rings", "Belts"
         - enable_flag is 1 for True (enable), 0 for False (disable)
         - Output: None
         - Example: > python3 backend_cli.py set_chaos_recipe_enabled_for Weapons 0 MyProfile
        '''
        CheckNumParams(function_params, 2)
        item_slot: str = function_params[0]
        enable_flag: bool = bool(int(function_params[1]))
        loot_filter.SetChaosRecipeEnabledFor(item_slot, enable_flag)
    elif (function_name == 'is_chaos_recipe_enabled_for'):
        '''
        is_chaos_recipe_enabled_for <item_slot: str>
         - <item_slot> is one of: "Weapons", "Body Armours", "Helmets", "Gloves",
           "Boots", "Amulets", "Rings", "Belts"  (defined in consts.py)
         - Output: "1" if chaos recipe items are showing for the given item_slot, else "0"
         - Example: > python3 backend_cli.py is_chaos_recipe_enabled_for "Body Armours" MyProfile
        '''
        CheckNumParams(function_params, 1)
        item_slot: str = function_params[0]
        output_string = str(int(loot_filter.IsChaosRecipeEnabledFor(item_slot)))
    elif (function_name == 'get_all_chaos_recipe_statuses'):
        '''
        get_all_chaos_recipe_statuses
         - Output: one line formatted as `<item_slot>;<enabled_flag>` for each item slot
         - <item_slot> is one of: "Weapons", "Body Armours", "Helmets", "Gloves",
           "Boots", "Amulets", "Rings", "Belts"
         - <enabled_flag> is "1" if chaos recipe items are showing for given item_slot, else "0"
         - Example: > python3 backend_cli.py get_all_chaos_recipe_statuses MyProfile
        '''
        CheckNumParams(function_params, 0)
        for item_slot in consts.kItemSlots:
            enabled_flag_string = str(int(loot_filter.IsChaosRecipeEnabledFor(item_slot)))
            output_string += item_slot + ';' + enabled_flag_string + '\n'
        if (output_string[-1] == '\n'): output_string = output_string[:-1]  # remove final newline
    # ================================= Unmatched Function Name =================================
    else:
        error_message: str = 'command not supported: {} {}'.format(
                function_name, JoinParams(function_params))
        logger.Log('Error: ' + error_message)
        raise RuntimeError(error_message)
    # ============================= End Function Call Delegation ================================
    # Return value is now in output_string
    if (in_batch):
        if (not suppress_output): AppendFunctionOutput(output_string)
    else:
        # If function was not run_batch, write output
        if (function_name != 'run_batch'):
            WriteOutput(output_string)
        # Save loot filter if we called a mutator function
        if (kFunctionInfoMap[function_name]['ModifiesFilter']):
            loot_filter.SaveToFile()
    # Save function call to Profile.changes if it is a mutator function.
    # This happens after function call processing, because that code may add default arguments.
    # Note: suppress_output also functioning as an indicator to not save profile data here.
    if (kFunctionInfoMap[function_name]['ModifiesFilter'] and not suppress_output):
        # We use the syntax some_list[:] to create a copy of some_list
        UpdateProfileChangesFile(config_values['ChangesFullpath'], function_name, function_params[:])
# End DelegateFunctionCall

kUsageSyntaxString = ('Usage synax:\n'
    '> python backend_cli.py <function_name> <function_arguments...> <profile_name (if required)>')

# Returns function_name, function_params, profile_name.
# If there is no profile param, returned profile_name is None.
def ValidateAndParseArguments() -> Tuple[str, List[str], str]:
    # Always require at least 2 params: script_name, function_name
    if (len(sys.argv) < 2):
        Error('No function specified\n' + kUsageSyntaxString)
    _, function_name, *remaining_params= sys.argv
    function_params = []
    # Separate remaining_params into function_params and profile_name
    # If no profile param, profile_name will be None
    profile_name = None
    if (kFunctionInfoMap[function_name]['HasProfileParam']):
        if (len(remaining_params) == 0):
            Error('No profile specified\n' + kUsageSyntaxString)
        *function_params, profile_name = remaining_params
    else: 
        function_params = remaining_params
    # Check if number of params is valid for given function name
    if (len(function_params) not in kFunctionInfoMap[function_name]['NumParamsOptions']):
        Error('Invalid number of parameters given ({}) for function {}\n'.format(
                len(function_params), function_name) + kUsageSyntaxString)
    # Check if profile exists, if there is a profile param
    if ((profile_name != None) and not profile.ProfileExists(profile_name)):
        Error('profile "{}" does not exist\n'.format(profile_name) + kUsageSyntaxString)
    return function_name, function_params, profile_name
# End ValidateAndParseArguments
    
def main_impl():
    # Initialize log
    logger.InitializeLog(kLogFilename)
    argv_info_message: str = 'Info: sys.argv = ' + str(sys.argv)
    logger.Log(argv_info_message)
    function_name, function_params, profile_name = ValidateAndParseArguments()
    # Set input filter source based on function name
    input_filter_source = (
            InputFilterSource.kDownload if (function_name == 'import_downloaded_filter')
            else InputFilterSource.kInput if (function_name == 'load_input_filter')
            else InputFilterSource.kOutput)
    # Delegate function call:
    # We create the loot filter first and pass in as a parameter, so that
    # DelegateFunctionCall can call itself recursively in run_batch.
    loot_filter = LootFilter(profile_name, input_filter_source) if profile_name else None
    DelegateFunctionCall(loot_filter, function_name, function_params)
# End main_impl

# Wrap the main_impl in a try-except block, so we can detect any error
# and notify the frontend via backend_cli.exit_code
def main():
    file_helper.WriteToFile('-1', kExitCodeFilename)  # -1 = In-progress exit code
    try:
        main_impl()
    except Exception as e:
        traceback_message = traceback.format_exc()
        logger.Log(traceback_message)
        file_helper.WriteToFile('1', kExitCodeFilename)  # 1 = Generic error exit code
        raise e
    file_helper.WriteToFile('0', kExitCodeFilename)  # 0 = Success exit code

if (__name__ == '__main__'):
    main()
