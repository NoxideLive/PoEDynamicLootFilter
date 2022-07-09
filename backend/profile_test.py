import profile

import file_helper
import os.path
from test_assertions import AssertEqual, AssertTrue, AssertFalse
import test_consts
import test_helper

def SetUp():
    TearDown()
    test_helper.SetUp(create_profile=False)

def TearDown():
    test_helper.TearDown()
    # Remove '.config', '.changes', and '.rules' files generated by test Profiles
    for profile_name in test_consts.kTestProfileNames:
        file_helper.RemoveFileIfExists(os.path.join(
            profile.kProfileDirectory, profile_name + '.config'))
        file_helper.RemoveFileIfExists(os.path.join(
            profile.kProfileDirectory, profile_name + '.changes'))
        file_helper.RemoveFileIfExists(os.path.join(
            profile.kProfileDirectory, profile_name + '.rules'))

def TestConfigChangesRulesPaths():
    profile_name = test_consts.kTestProfileName
    expected_fullpath_stem = os.path.join(profile.kProfileDirectory, profile_name)
    # Config
    config_fullpath = profile.GetProfileConfigFullpath(profile_name)
    AssertEqual(config_fullpath, expected_fullpath_stem + '.config')
    # Changes
    changes_fullpath = profile.GetProfileChangesFullpath(profile_name)
    AssertEqual(changes_fullpath, expected_fullpath_stem + '.changes')
    # Rules
    rules_fullpath = profile.GetProfileRulesFullpath(profile_name)
    AssertEqual(rules_fullpath, expected_fullpath_stem + '.rules')
    print('TestConfigChangesRulesPaths passed!')

def TestListProfileNames():
    expected_profile_names = []
    for f in os.listdir(profile.kProfileDirectory):
        filestem, extension = os.path.splitext(f)
        if ((extension == '.config') and (filestem != 'general')):
            expected_profile_names.append(filestem)
    profile_names = profile.ListProfilesRaw()
    AssertEqual(sorted(profile_names), sorted(expected_profile_names))
    print('TestListProfileNames passed!')

def TestCreateRenameDeleteProfile():
    print('TestCreateRenameDeleteProfile')
    SetUp()
    profile_name = test_consts.kTestProfileName
    AssertFalse(profile.ProfileExists(profile_name))
    # Create profile
    created_profile = profile.CreateNewProfile(profile_name, test_consts.kTestProfileConfigValues)
    AssertTrue(created_profile != None)
    AssertTrue(created_profile.name == profile_name)
    AssertTrue(profile.ProfileExists(profile_name))
    # Rename profile
    new_profile_name = 'TestProfile_EketPW7aflDMiJ220H7M'
    profile.RenameProfile(profile_name, new_profile_name)
    AssertFalse(profile.ProfileExists(profile_name))
    AssertTrue(profile.ProfileExists(new_profile_name))
    # Delete profile
    profile.DeleteProfile(new_profile_name)
    AssertFalse(profile.ProfileExists(new_profile_name))
    print('TestCreateRenameDeleteProfile passed!')

def TestSetGetActiveProfile():
    SetUp()
    profile_name = test_consts.kTestProfileNames[1]
    other_profile_name = test_consts.kTestProfileNames[2]
    # Create profile and verify it is the active profile after creation
    profile.CreateNewProfile(profile_name, test_consts.kTestProfileConfigValues)
    AssertTrue(profile.ProfileExists(profile_name))
    AssertEqual(profile.GetActiveProfileName(), profile_name)
    # Create other profile similarly
    profile.CreateNewProfile(other_profile_name, test_consts.kTestProfileConfigValues)
    AssertTrue(profile.ProfileExists(other_profile_name))
    AssertEqual(profile.GetActiveProfileName(), other_profile_name)
    # Set/get active profile (profile_name)
    profile.SetActiveProfile(profile_name)
    active_profile_name = profile.GetActiveProfileName()
    AssertEqual(active_profile_name, profile_name)
    # Set/get active profile (other_profile_name)
    profile.SetActiveProfile(other_profile_name)
    active_profile_name = profile.GetActiveProfileName()
    AssertEqual(active_profile_name, other_profile_name)
    # Check GetAllProfileNames() returns active profile first
    get_all_profile_names_result = profile.GetAllProfileNames()
    AssertTrue(len(get_all_profile_names_result) >= 2)
    AssertEqual(get_all_profile_names_result[0], active_profile_name)
    # Verify that both profile names are present
    AssertTrue(profile_name in get_all_profile_names_result)
    AssertTrue(other_profile_name in get_all_profile_names_result)
    # Delete profiles and verify they are not still the active profile
    profile.DeleteProfile(profile_name)
    profile.DeleteProfile(other_profile_name)
    AssertTrue(profile.GetActiveProfileName() not in (profile_name, other_profile_name))
    print('TestSetGetActiveProfile passed!')

'''
Exampe Profile config_values:

ProfileName : DefaultProfile (str)
DownloadDirectory : FiltersDownload (str)
InputLootFilterDirectory : FiltersInput (str) (derived)
PathOfExileDirectory : FiltersPathOfExile (str)
DownloadedLootFilterFilename : BrandLeaguestart.filter (str)
OutputLootFilterFilename : DynamicLootFilter.filter (str)
RemoveDownloadedFilter : False (bool)
HideMapsBelowTier : 0 (int)  # To be removed in future
AddChaosRecipeRules : True (str)  # To be removed in future
ChaosRecipeWeaponClassesAnyHeight : "Daggers" "Rune Daggers" "Wands" (str)
ChaosRecipeWeaponClassesMaxHeight3 : "Bows" (str)
DownloadedLootFilterFullpath : FiltersDownload/BrandLeaguestart.filter (str) (derived)
InputLootFilterFullpath : FiltersInput/BrandLeaguestart.filter (str) (derived)
OutputLootFilterFullpath : FiltersPathOfExile/DynamicLootFilter.filter (str) (derived)
'''
def TestParseProfile():
    SetUp()
    profile_name = test_consts.kTestProfileName
    created_profile = profile.CreateNewProfile(profile_name, test_consts.kTestProfileConfigValues)
    AssertEqual(created_profile.config_values['ProfileName'], profile_name)
    # Check required config values match exactly
    AssertEqual(created_profile.config_values['DownloadDirectory'],
            test_consts.kTestProfileConfigValues['DownloadDirectory'])
    AssertEqual(created_profile.config_values['PathOfExileDirectory'],
            test_consts.kTestProfileConfigValues['PathOfExileDirectory'])
    AssertEqual(created_profile.config_values['DownloadedLootFilterFilename'],
            test_consts.kTestProfileConfigValues['DownloadedLootFilterFilename'])
    # Check input directory config value exists
    AssertTrue('InputLootFilterDirectory' in created_profile.config_values)
    AssertEqual(created_profile.config_values['OutputLootFilterFilename'], 'DynamicLootFilter.filter')
    AssertTrue(created_profile.config_values['RemoveDownloadedFilter'] in (True, False))
    # Check Chaos params exist
    AssertTrue('ChaosRecipeWeaponClassesAnyHeight' in created_profile.config_values)
    AssertTrue('ChaosRecipeWeaponClassesMaxHeight3' in created_profile.config_values)
    # Check derived paths are correct
    expected_downloaded_filter_fullpath = os.path.join(
            test_consts.kTestProfileConfigValues['DownloadDirectory'],
            test_consts.kTestProfileConfigValues['DownloadedLootFilterFilename'])
    AssertEqual(created_profile.config_values['DownloadedLootFilterFullpath'],
                expected_downloaded_filter_fullpath)
    expected_input_filter_fullpath = os.path.join(
            created_profile.config_values['InputLootFilterDirectory'],
            test_consts.kTestProfileConfigValues['DownloadedLootFilterFilename'])
    AssertEqual(created_profile.config_values['InputLootFilterFullpath'],
                expected_input_filter_fullpath)
    expected_output_filter_fullpath = os.path.join(
            created_profile.config_values['PathOfExileDirectory'], 'DynamicLootFilter.filter')
    AssertEqual(created_profile.config_values['OutputLootFilterFullpath'],
                expected_output_filter_fullpath)
    # Cleanup: delete test profile
    profile.DeleteProfile(profile_name)
    print('TestParseProfile passed!')

def TestWriteProfile():
    SetUp()
    profile_name = test_consts.kTestProfileName
    created_profile = profile.CreateNewProfile(profile_name, test_consts.kTestProfileConfigValues)
    config_lines = file_helper.ReadFile(
            profile.GetProfileConfigFullpath(profile_name), strip=True)
    # Just verify the paths for simplicity (doesn't test everything):
    expected_download_directory_line = 'Download directory: {}'.format(
            test_consts.kTestProfileConfigValues['DownloadDirectory'])
    expected_poe_directory_line = 'Path of Exile directory: {}'.format(
            test_consts.kTestProfileConfigValues['PathOfExileDirectory'])
    expected_downloaded_filter_filename_line = 'Downloaded loot filter filename: {}'.format(
            test_consts.kTestProfileConfigValues['DownloadedLootFilterFilename'])
    expected_output_filter_filename_line = \
            'Output (Path of Exile) loot filter filename: DynamicLootFilter.filter'
    AssertTrue(expected_download_directory_line in config_lines)
    AssertTrue(expected_poe_directory_line in config_lines)
    AssertTrue(expected_downloaded_filter_filename_line in config_lines)
    AssertTrue(expected_output_filter_filename_line in config_lines)
    # Cleanup: delete test profile
    profile.DeleteProfile(profile_name)
    print('TestWriteProfile passed!')

def main():
    TestConfigChangesRulesPaths()
    TestListProfileNames()
    TestCreateRenameDeleteProfile()
    TestSetGetActiveProfile()
    TestParseProfile()
    TestWriteProfile()
    TearDown()
    print('All tests passed!')

if (__name__ == '__main__'):
    main()